"""Tests for jd_summary_enrichment service + endpoints (PR #41).

Mirrors the test structure of ``test_division_enrichment.py``:
pure-function tests for prompt + validator, DB-gated tests for the
six-status state machine and the sweep. Gemini is monkey-patched so
no test ever hits the real SDK.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from job_assist.db.models import JobPosting, TargetCompany
from job_assist.services.jd_summary_enrichment import (
    EnrichmentResult,
    SweepSummary,
    _validate_summary,
    build_prompt,
    enrich_one_posting,
    get_system_prompt,
    reset_attempts_and_retry,
    sweep_jd_summaries,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Sync helpers ─────────────────────────────────────────────────────────────


_SAMPLE_JD = (
    "Senior Product Manager — Risk Platform\n\n"
    "We're hiring a Senior PM to own fraud-detection signals at ExampleCo. "
    "You'll partner with engineering, data science, and ops to ship "
    "machine-learning-backed defenses against account takeover.\n\n"
    "Requirements:\n"
    "- 5+ years of product management experience\n"
    "- Experience shipping ML-backed consumer features\n"
    "Nice-to-haves:\n"
    "- SQL fluency\n"
    "- Prior fraud / risk domain experience\n\n"
    "Compensation: $180k-$220k base. Remote-friendly with quarterly NYC visits."
)


def _company() -> TargetCompany:
    return TargetCompany(
        name=f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(
    *,
    target_company_id: uuid.UUID,
    jd_text: str = _SAMPLE_JD,
    summary: str | None = None,
    attempt_count: int = 0,
) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        jd_text=jd_text,
        jd_text_hash=("0" * 63 + suffix[0]),
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        jd_summary_markdown=summary,
        jd_summary_enrichment_attempt_count=attempt_count,
    )


def _patch_generate(monkeypatch: pytest.MonkeyPatch, text_or_exc: Any) -> None:
    """Replace generate_summary with a stub returning the value or raising."""

    async def _stub(*_args: Any, **_kwargs: Any) -> str:
        if isinstance(text_or_exc, Exception):
            raise text_or_exc
        return text_or_exc  # type: ignore[no-any-return]

    monkeypatch.setattr(
        "job_assist.services.jd_summary_enrichment.generate_summary",
        _stub,
    )


# ── 12. Pure: prompt template ────────────────────────────────────────────────


def test_prompt_template_includes_ambiguity_instruction() -> None:
    """The system prompt MUST tell the model to flag ambiguities — that's the
    load-bearing piece of the whole feature."""
    sysprompt = get_system_prompt()
    assert "AMBIGUITIES" in sysprompt
    assert "Do NOT smooth over ambiguity" in sysprompt
    # Examples must remain in the prompt — they shape the model's behavior.
    assert "5+ years required" in sysprompt
    assert "Senior PM" in sysprompt
    # Output structure markers must remain.
    for label in (
        "**Scope**:",
        "**Org context**:",
        "**Hard requirements**:",
        "**Nice-to-haves**:",
        "**Comp**:",
        "**Location**:",
        "**Ambiguities**:",
    ):
        assert label in sysprompt


def test_build_prompt_includes_jd_text() -> None:
    user_message = build_prompt("Hello world JD content")
    assert "Hello world JD content" in user_message
    assert "Job description follows" in user_message


def test_build_prompt_trims_whitespace() -> None:
    user_message = build_prompt("   trimmed   ")
    assert "trimmed" in user_message
    assert "   trimmed   " not in user_message


# ── 13. Pure: response validator ─────────────────────────────────────────────


class TestValidateSummary:
    def test_strips_whitespace(self) -> None:
        assert _validate_summary("  hello  ") == "hello"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _validate_summary("   ")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            _validate_summary("x" * 5000)

    def test_accepts_markdown_with_newlines(self) -> None:
        """Unlike company / division descriptions, JD summaries are
        multi-line markdown — newlines are allowed."""
        body = "**Scope**: foo\n**Comp**: bar"
        assert _validate_summary(body) == body

    def test_strips_nul_bytes(self) -> None:
        """PR #44 fix: Postgres TEXT rejects NUL bytes. Gemini occasionally
        embeds them; we strip rather than reject so a single bad byte
        doesn't waste the call."""
        body = "**Scope**: \x00fraud\x00 signals."
        assert _validate_summary(body) == "**Scope**: fraud signals."

    def test_strips_other_c0_controls(self) -> None:
        """Other C0 controls (except \\n and \\t) get stripped too."""
        body = "**Scope**: \x01\x02fraud\x07 signals.\n**Comp**: \x08\x1f$200k."
        assert _validate_summary(body) == "**Scope**: fraud signals.\n**Comp**: $200k."

    def test_preserves_newlines_and_tabs(self) -> None:
        body = "**Scope**:\tfraud signals.\n**Comp**:\t$200k."
        assert _validate_summary(body) == body

    def test_rejects_empty_after_strip(self) -> None:
        """If a response is all control characters, the strip empties it
        and we treat that the same as an empty response."""
        with pytest.raises(ValueError, match="empty after"):
            _validate_summary("\x00\x01\x02\x03")


def test_sweep_summary_record_classifies_each_status() -> None:
    summary = SweepSummary()
    summary.record(EnrichmentResult(status="enriched", posting_id="a"))
    summary.record(EnrichmentResult(status="skipped", posting_id="b"))
    summary.record(EnrichmentResult(status="exhausted", posting_id="c"))
    summary.record(EnrichmentResult(status="missing_context", posting_id="d"))
    summary.record(EnrichmentResult(status="error", posting_id="e", error="boom"))
    assert summary.total == 5
    assert summary.enriched == 1
    assert summary.skipped == 1
    assert summary.exhausted == 1
    assert summary.missing_context == 1
    assert summary.errors == 1
    assert summary.error_details[0]["posting_id"] == "e"


# ── DB-gated: state machine ──────────────────────────────────────────────────


# ── 1 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_enrich_one_skipped_when_already_enriched(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id, summary="**Scope**: existing")
    db_session.add(posting)
    await db_session.commit()

    # No monkey-patch — if the service tried to call Gemini we'd get an
    # ImportError or auth error, not a clean "skipped".
    result = await enrich_one_posting(db_session, posting.id)
    assert result.status == "skipped"


# ── 2 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_enrich_one_exhausted_when_max_attempts_reached(
    db_session: Any,
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id, attempt_count=3)
    db_session.add(posting)
    await db_session.commit()

    result = await enrich_one_posting(db_session, posting.id)
    assert result.status == "exhausted"


# ── 3 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_enrich_one_missing_context_when_no_jd_text(
    db_session: Any,
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id, jd_text="too short")
    db_session.add(posting)
    await db_session.commit()

    result = await enrich_one_posting(db_session, posting.id)
    assert result.status == "missing_context"

    # Attempt counter must increment so the row eventually goes exhausted.
    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting.id))
    ).scalar_one()
    assert refreshed.jd_summary_enrichment_attempt_count == 1
    assert refreshed.jd_summary_enrichment_error is not None


# ── 4 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_enrich_one_enriched_on_success(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id)
    db_session.add(posting)
    await db_session.commit()

    fake_md = "**Scope**: Senior PM owns fraud signals.\n**Comp**: $180k-$220k."
    _patch_generate(monkeypatch, fake_md)

    result = await enrich_one_posting(db_session, posting.id)
    assert result.status == "enriched"

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting.id))
    ).scalar_one()
    assert refreshed.jd_summary_markdown == fake_md
    assert refreshed.jd_summary_enriched_at is not None
    assert refreshed.jd_summary_enrichment_error is None


# ── 5 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_enrich_one_increments_attempts_on_error(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id)
    db_session.add(posting)
    await db_session.commit()

    _patch_generate(monkeypatch, RuntimeError("rate limited"))

    result = await enrich_one_posting(db_session, posting.id)
    assert result.status == "error"
    assert result.error is not None and "rate limited" in result.error

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting.id))
    ).scalar_one()
    assert refreshed.jd_summary_enrichment_attempt_count == 1
    assert refreshed.jd_summary_markdown is None


# ── 6 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_sweep_iterates_eligible_postings(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    db_session.add_all(
        [
            _posting(target_company_id=company.id),
            _posting(target_company_id=company.id, summary="**already enriched**"),
            _posting(target_company_id=company.id, attempt_count=99),
        ]
    )
    await db_session.commit()

    _patch_generate(monkeypatch, "**Scope**: fresh.")

    summary = await sweep_jd_summaries(db_session)
    # Already-enriched + exhausted rows are filtered at the SQL level,
    # so the sweep only sees the one eligible row.
    assert summary.total == 1
    assert summary.enriched == 1


# ── 7 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_sweep_returns_correct_counts(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    db_session.add_all(
        [
            _posting(target_company_id=company.id),
            _posting(target_company_id=company.id),
            _posting(target_company_id=company.id, jd_text="x"),  # missing_context
        ]
    )
    await db_session.commit()

    _patch_generate(monkeypatch, "**Scope**: fresh.")
    summary = await sweep_jd_summaries(db_session)
    assert summary.total == 3
    assert summary.enriched == 2
    assert summary.missing_context == 1


# ── 8 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_retry_resets_attempts_and_clears_summary(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(
        target_company_id=company.id,
        summary="**stale**",
        attempt_count=3,
    )
    db_session.add(posting)
    await db_session.commit()

    _patch_generate(monkeypatch, "**Scope**: refreshed.")

    result = await reset_attempts_and_retry(db_session, posting.id)
    assert result.status == "enriched"

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting.id))
    ).scalar_one()
    assert refreshed.jd_summary_markdown == "**Scope**: refreshed."
    assert refreshed.jd_summary_enrichment_attempt_count == 0
    assert refreshed.jd_summary_enrichment_error is None


# ── 9 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_sweep_respects_limit_parameter(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    db_session.add_all([_posting(target_company_id=company.id) for _ in range(5)])
    await db_session.commit()

    _patch_generate(monkeypatch, "**Scope**: capped.")

    summary = await sweep_jd_summaries(db_session, limit=2)
    assert summary.total == 2
    assert summary.enriched == 2


# ── 10 — ordering (Bestiary 5.20) ─────────────────────────────────────────────
@_NEEDS_DB
async def test_sweep_orders_by_attempts_then_first_seen(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep must process never-tried-first (attempt_count ASC), then
    oldest-first within a tier (first_seen_at ASC) — Bestiary 5.20. The old
    ``first_seen_at DESC`` ordering starved the backlog tail.

    Setup: one attempt=2 row that is globally the OLDEST, plus three
    attempt=0 rows (oldest/mid/newest). With ``limit=2`` the sweep must pick
    the two LOWEST-attempt + oldest rows — the attempt=0 oldest and mid —
    proving attempt_count is the primary key (the attempt=2 row is skipped
    despite being globally oldest) and first_seen_at is the tiebreaker.
    """
    company = _company()
    db_session.add(company)
    await db_session.flush()

    base = datetime(2026, 1, 1, tzinfo=UTC)

    def _at(attempt: int, days: int) -> JobPosting:
        p = _posting(target_company_id=company.id, attempt_count=attempt)
        p.first_seen_at = base + timedelta(days=days)
        return p

    a2_oldest = _at(2, 0)  # lowest first_seen but highest attempt → last
    a0_old = _at(0, 1)  # ← expected #1 (attempt0, oldest in tier)
    a0_mid = _at(0, 2)  # ← expected #2
    a0_new = _at(0, 3)  # attempt0 newest → not reached at limit=2
    db_session.add_all([a2_oldest, a0_old, a0_mid, a0_new])
    await db_session.flush()
    ids = {
        "a2_oldest": a2_oldest.id,
        "a0_old": a0_old.id,
        "a0_mid": a0_mid.id,
        "a0_new": a0_new.id,
    }
    await db_session.commit()

    _patch_generate(monkeypatch, "**Scope**: ordered.")

    summary = await sweep_jd_summaries(db_session, limit=2)
    assert summary.total == 2
    assert summary.enriched == 2

    # Read back which rows actually got a summary.
    rows = (await db_session.execute(select(JobPosting))).scalars().all()
    enriched_ids = {r.id for r in rows if r.jd_summary_markdown is not None}

    assert enriched_ids == {ids["a0_old"], ids["a0_mid"]}, (
        "sweep must process the two attempt=0 oldest rows first; "
        "the attempt=2 row (globally oldest) and the attempt=0 newest must wait"
    )


# ── 10. Migration: four columns exist ────────────────────────────────────────
@_NEEDS_DB
async def test_migration_adds_four_columns(db_session: Any) -> None:
    cols = (
        (
            await db_session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'job_posting' AND column_name LIKE 'jd_summary_%'"
                )
            )
        )
        .scalars()
        .all()
    )
    expected = {
        "jd_summary_markdown",
        "jd_summary_enriched_at",
        "jd_summary_enrichment_error",
        "jd_summary_enrichment_attempt_count",
    }
    assert expected.issubset(set(cols))


# ── 11. Migration: defaults on existing rows ─────────────────────────────────
@_NEEDS_DB
async def test_migration_existing_rows_have_correct_defaults(
    db_session: Any,
) -> None:
    """A fresh row with no jd_summary_* values set takes the DB-side defaults:
    NULLs on the nullable columns, 0 on the attempt counter."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    suffix = uuid.uuid4().hex[:8]
    await db_session.execute(
        sa.text(
            "INSERT INTO job_posting "
            "(id, canonical_company_name, target_company_id, normalized_title, "
            "raw_title, jd_text, jd_text_hash, content_hash) "
            "VALUES "
            "(gen_random_uuid(), 'TestCo', :tcid, 'foo', 'Foo', 'x', :h, :c)"
        ),
        {
            "tcid": company.id,
            "h": "0" * 64,
            "c": f"raw-defaults-{suffix}",
        },
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            sa.text(
                "SELECT jd_summary_markdown, jd_summary_enriched_at, "
                "jd_summary_enrichment_error, jd_summary_enrichment_attempt_count "
                "FROM job_posting WHERE content_hash = :c"
            ),
            {"c": f"raw-defaults-{suffix}"},
        )
    ).first()
    assert row is not None
    assert row.jd_summary_markdown is None
    assert row.jd_summary_enriched_at is None
    assert row.jd_summary_enrichment_error is None
    assert row.jd_summary_enrichment_attempt_count == 0
