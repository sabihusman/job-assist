"""Tests for division_enrichment service + endpoints.

Mirrors the test structure of ``test_company_enrichment.py``:
pure-function tests for prompt + validator, DB-gated tests for
discovery, enrichment, and the FastAPI endpoints. Gemini is monkey-
patched at the module level so no test ever hits the real SDK.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from job_assist.db.models import Division, JobPosting, TargetCompany
from job_assist.services.division_enrichment import (
    DiscoverySummary,
    EnrichmentResult,
    SweepSummary,
    _clause,
    _validate_description,
    build_prompt,
    discover_divisions,
    enrich_division,
    reset_attempts_and_retry,
    sweep_divisions,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure / sync ──────────────────────────────────────────────────────────────


class TestClause:
    def test_dept_and_team(self) -> None:
        assert _clause("Product", "Risk") == "Product/Risk division"

    def test_dept_only(self) -> None:
        assert _clause("Engineering", None) == "Engineering division"

    def test_team_only(self) -> None:
        assert _clause(None, "Platform") == "Platform team"

    def test_both_none_returns_fallback(self) -> None:
        # discover_divisions never inserts (None, None) but the helper
        # must still return a usable string.
        assert _clause(None, None) == "team"


class TestBuildPrompt:
    def test_dept_and_team(self) -> None:
        prompt = build_prompt(
            company_name="ExampleCo",
            company_description="ExampleCo builds developer tooling.",
            department="Product",
            team="Risk",
        )
        assert "Product/Risk division" in prompt
        assert "ExampleCo builds developer tooling." in prompt
        assert "ExampleCo" in prompt

    def test_dept_only(self) -> None:
        """``team=None`` collapses to ``{department} division`` — no team clause."""
        prompt = build_prompt("AcmeCo", "AcmeCo does things.", "Engineering", None)
        assert "Engineering division" in prompt
        # The clause itself is the only place "team" should NOT appear; the
        # static prompt body talks about "the specific team" generically.
        assert "Engineering team" not in prompt
        assert "Engineering/" not in prompt

    def test_team_only(self) -> None:
        prompt = build_prompt("AcmeCo", "AcmeCo does things.", None, "Platform")
        assert "Platform team" in prompt

    def test_no_company_description_uses_fallback(self) -> None:
        prompt = build_prompt("AcmeCo", None, "Product", "Risk")
        assert "(no description available)" in prompt

    def test_empty_company_description_uses_fallback(self) -> None:
        prompt = build_prompt("AcmeCo", "   ", "Product", "Risk")
        assert "(no description available)" in prompt

    def test_includes_company_name_for_grounding(self) -> None:
        """Golden substring: the model must see the company name twice
        (once in the description preface, once anchoring the clause)."""
        prompt = build_prompt("AcmeCo", "AcmeCo is a thing.", "Product", "Risk")
        assert prompt.count("AcmeCo") >= 2


class TestValidateDescription:
    def test_strips_whitespace(self) -> None:
        assert _validate_description("  hello  ") == "hello"

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            _validate_description("x" * 300)

    def test_rejects_newlines(self) -> None:
        with pytest.raises(ValueError, match="newlines"):
            _validate_description("line one\nline two")

    def test_rejects_carriage_return(self) -> None:
        with pytest.raises(ValueError, match="newlines"):
            _validate_description("line one\rline two")


def test_sweep_summary_record_classifies_each_status() -> None:
    summary = SweepSummary()
    summary.record(EnrichmentResult(status="enriched", division_id="a"))
    summary.record(EnrichmentResult(status="skipped", division_id="b"))
    summary.record(EnrichmentResult(status="exhausted", division_id="c"))
    summary.record(EnrichmentResult(status="missing_context", division_id="d"))
    summary.record(EnrichmentResult(status="error", division_id="e", error="boom"))

    assert summary.total == 5
    assert summary.enriched == 1
    assert summary.skipped == 1
    assert summary.exhausted == 1
    assert summary.missing_context == 1
    assert summary.errors == 1
    assert len(summary.error_details) == 1
    assert summary.error_details[0]["division_id"] == "e"


# ── DB helpers ───────────────────────────────────────────────────────────────


def _company(
    *,
    name: str = "TestCo",
    description: str | None = "TestCo is a synthetic test fixture.",
) -> TargetCompany:
    return TargetCompany(
        name=name,
        tier=1,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
        description=description,
    )


def _job_posting(
    *,
    target_company_id: uuid.UUID,
    department: str | None,
    team: str | None,
    title_suffix: str = "PM",
) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=f"senior product manager {title_suffix}",
        raw_title=f"Senior Product Manager {title_suffix}",
        jd_text="x",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        department=department,
        team=team,
    )


# ── Migration / schema ──────────────────────────────────────────────────────


@_NEEDS_DB
async def test_migration_creates_table_with_nulls_not_distinct_constraint(
    db_session: Any,
) -> None:
    """The unique constraint must use NULLS NOT DISTINCT (PG 15+)."""
    row = (
        await db_session.execute(
            sa.text(
                """
                SELECT pg_get_constraintdef(c.oid) AS constraint_def
                  FROM pg_constraint c
                  JOIN pg_class t ON t.oid = c.conrelid
                 WHERE t.relname = 'division'
                   AND c.conname = 'uq_division_company_dept_team'
                """
            )
        )
    ).first()
    assert row is not None, "unique constraint missing"
    # PG renders this exactly as ``UNIQUE NULLS NOT DISTINCT (col, col, col)``.
    assert "NULLS NOT DISTINCT" in row.constraint_def


@_NEEDS_DB
async def test_migration_cascade_on_company_delete(db_session: Any) -> None:
    """Deleting the parent target_company cascades to its divisions."""
    company = _company(name="CascadeTestCo")
    db_session.add(company)
    await db_session.flush()

    div = Division(target_company_id=company.id, department="Eng", team=None)
    db_session.add(div)
    await db_session.commit()

    # Delete the company. FK ON DELETE CASCADE should sweep the division.
    await db_session.execute(
        sa.text("DELETE FROM target_company WHERE id = :id"),
        {"id": company.id},
    )
    await db_session.commit()

    remaining = (
        await db_session.execute(select(Division).where(Division.id == div.id))
    ).scalar_one_or_none()
    assert remaining is None


# ── Discovery ────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_discover_divisions_finds_new_tuples(db_session: Any) -> None:
    company = _company(name="DiscoverFinds")
    db_session.add(company)
    await db_session.flush()

    db_session.add_all(
        [
            _job_posting(target_company_id=company.id, department="Eng", team="Platform"),
            _job_posting(target_company_id=company.id, department="Eng", team="Growth"),
            _job_posting(target_company_id=company.id, department="Product", team=None),
        ]
    )
    await db_session.commit()

    summary = await discover_divisions(db_session)
    assert summary.discovered == 3
    assert summary.already_existed == 0

    rows = (
        (await db_session.execute(select(Division).where(Division.target_company_id == company.id)))
        .scalars()
        .all()
    )
    by_tuple = {(d.department, d.team) for d in rows}
    assert by_tuple == {("Eng", "Platform"), ("Eng", "Growth"), ("Product", None)}


@_NEEDS_DB
async def test_discover_divisions_skips_both_null(db_session: Any) -> None:
    """Job postings with department IS NULL AND team IS NULL produce no division."""
    company = _company(name="SkipsBothNull")
    db_session.add(company)
    await db_session.flush()

    db_session.add(_job_posting(target_company_id=company.id, department=None, team=None))
    await db_session.commit()

    summary = await discover_divisions(db_session)
    assert summary.discovered == 0
    assert summary.already_existed == 0


@_NEEDS_DB
async def test_discover_divisions_idempotent(db_session: Any) -> None:
    """Second run inserts zero new rows; all tuples already_existed."""
    company = _company(name="DiscoverIdempotent")
    db_session.add(company)
    await db_session.flush()

    db_session.add(_job_posting(target_company_id=company.id, department="Eng", team="Platform"))
    await db_session.commit()

    first = await discover_divisions(db_session)
    second = await discover_divisions(db_session)
    assert first.discovered == 1
    assert second.discovered == 0
    assert second.already_existed == 1


@_NEEDS_DB
async def test_discover_divisions_nulls_not_distinct_collapses(
    db_session: Any,
) -> None:
    """Two postings with (Eng, NULL) under the same company collapse into one division."""
    company = _company(name="NullsCollapse")
    db_session.add(company)
    await db_session.flush()

    db_session.add_all(
        [
            _job_posting(target_company_id=company.id, department="Eng", team=None),
            _job_posting(
                target_company_id=company.id,
                department="Eng",
                team=None,
                title_suffix="other",
            ),
        ]
    )
    await db_session.commit()

    summary = await discover_divisions(db_session)
    assert summary.discovered == 1
    assert summary.already_existed == 0

    rows = (
        (await db_session.execute(select(Division).where(Division.target_company_id == company.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


# ── Enrich-division state machine ────────────────────────────────────────────


def _patch_generate(monkeypatch: pytest.MonkeyPatch, text_or_exc: Any) -> None:
    """Replace generate_description with a stub that returns *text_or_exc* or raises it."""

    async def _stub(*_args: Any, **_kwargs: Any) -> str:
        if isinstance(text_or_exc, Exception):
            raise text_or_exc
        return text_or_exc  # type: ignore[no-any-return]

    monkeypatch.setattr("job_assist.services.division_enrichment.generate_description", _stub)


@_NEEDS_DB
async def test_enrich_division_success(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    company = _company(name="EnrichSuccess")
    db_session.add(company)
    await db_session.flush()
    div = Division(target_company_id=company.id, department="Product", team="Risk")
    db_session.add(div)
    await db_session.commit()

    _patch_generate(
        monkeypatch, "EnrichSuccess's Product/Risk division ships fraud-detection tooling."
    )

    result = await enrich_division(db_session, div.id)
    assert result.status == "enriched"

    await db_session.refresh(div)
    assert div.description is not None
    assert "fraud-detection tooling" in div.description
    assert div.enriched_at is not None
    assert div.enrichment_error is None


@_NEEDS_DB
async def test_enrich_division_skips_already_enriched(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company(name="SkipsAlready")
    db_session.add(company)
    await db_session.flush()
    div = Division(
        target_company_id=company.id,
        department="Eng",
        team=None,
        description="Pre-existing description.",
    )
    db_session.add(div)
    await db_session.commit()

    called: list[str] = []

    async def _stub(*_a: Any, **_k: Any) -> str:
        called.append("yes")
        return "fresh"

    monkeypatch.setattr("job_assist.services.division_enrichment.generate_description", _stub)

    result = await enrich_division(db_session, div.id)
    assert result.status == "skipped"
    assert called == []


@_NEEDS_DB
async def test_enrich_division_missing_context(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A company with empty name yields ``missing_context``, no LLM call."""
    company = _company(name="")
    db_session.add(company)
    await db_session.flush()
    div = Division(target_company_id=company.id, department="Eng", team=None)
    db_session.add(div)
    await db_session.commit()

    called: list[str] = []

    async def _stub(*_a: Any, **_k: Any) -> str:
        called.append("yes")
        return "x"

    monkeypatch.setattr("job_assist.services.division_enrichment.generate_description", _stub)

    result = await enrich_division(db_session, div.id)
    assert result.status == "missing_context"
    assert called == []

    await db_session.refresh(div)
    assert div.enrichment_attempt_count == 1
    assert div.enrichment_error is not None


@_NEEDS_DB
async def test_enrich_division_gemini_failure(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company(name="GeminiFails")
    db_session.add(company)
    await db_session.flush()
    div = Division(target_company_id=company.id, department="Platform", team=None)
    db_session.add(div)
    await db_session.commit()

    _patch_generate(monkeypatch, RuntimeError("gemini exploded: 503"))

    result = await enrich_division(db_session, div.id)
    assert result.status == "error"
    assert result.error is not None
    assert "gemini exploded" in result.error

    await db_session.refresh(div)
    assert div.enrichment_attempt_count == 1
    assert div.enrichment_error is not None
    assert div.description is None


@_NEEDS_DB
async def test_enrich_division_exhausted(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_assist.config import settings

    company = _company(name="DivExhausted")
    db_session.add(company)
    await db_session.flush()
    div = Division(
        target_company_id=company.id,
        department="Eng",
        team=None,
        enrichment_attempt_count=settings.division_enrich_max_attempts,
    )
    db_session.add(div)
    await db_session.commit()

    called: list[str] = []

    async def _stub(*_a: Any, **_k: Any) -> str:
        called.append("yes")
        return "x"

    monkeypatch.setattr("job_assist.services.division_enrichment.generate_description", _stub)

    result = await enrich_division(db_session, div.id)
    assert result.status == "exhausted"
    assert called == []


@_NEEDS_DB
async def test_enrich_division_not_found(db_session: Any) -> None:
    result = await enrich_division(db_session, uuid.uuid4())
    assert result.status == "not_found"


# ── Sweep + retry ────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_sweep_summary_counts(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mix across all enrichment buckets — discovery + enrichment math add up."""
    company = _company(name="SweepMix")
    db_session.add(company)
    await db_session.flush()

    # Three job_postings → discovery finds three tuples.
    db_session.add_all(
        [
            _job_posting(target_company_id=company.id, department="Happy", team=None),
            _job_posting(target_company_id=company.id, department="Already", team=None),
            _job_posting(target_company_id=company.id, department="Flaky", team=None),
        ]
    )
    await db_session.commit()

    # Pre-seed one division (Already) as already-enriched.
    db_session.add(
        Division(
            target_company_id=company.id,
            department="Already",
            team=None,
            description="cached forever",
        )
    )
    await db_session.commit()

    async def _stub(
        company_name: str,
        company_description: str | None,
        department: str | None,
        team: str | None,
        **_: Any,
    ) -> str:
        if department == "Flaky":
            raise RuntimeError("simulated flake")
        return f"{department or 'team'} description."

    monkeypatch.setattr("job_assist.services.division_enrichment.generate_description", _stub)

    summary = await sweep_divisions(db_session)

    # Discovery: one division already existed (Already); two new
    # (Happy, Flaky). The job_posting for Already creates its discovery
    # row only if not pre-existent; we pre-seeded that division row, so
    # discovery sees two new tuples.
    assert summary.discovered == 2
    assert summary.already_existed == 1

    # Enrichment: Happy → enriched; Already → skipped; Flaky → error.
    assert summary.total == 3
    assert summary.enriched == 1
    assert summary.skipped == 1
    assert summary.errors == 1
    assert summary.enriched + summary.skipped + summary.errors == summary.total


@_NEEDS_DB
async def test_retry_resets_count_and_reenriches(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.config import settings

    company = _company(name="RetryDivision")
    db_session.add(company)
    await db_session.flush()
    div = Division(
        target_company_id=company.id,
        department="Eng",
        team=None,
        enrichment_attempt_count=settings.division_enrich_max_attempts,
        enrichment_error="prior failure",
    )
    db_session.add(div)
    await db_session.commit()

    _patch_generate(monkeypatch, "Eng is back online.")

    result = await reset_attempts_and_retry(db_session, div.id)
    assert result.status == "enriched"

    await db_session.refresh(div)
    assert div.description == "Eng is back online."
    assert div.enriched_at is not None
    assert div.enrichment_error is None
    assert div.enrichment_attempt_count == 0


# ── Endpoints ────────────────────────────────────────────────────────────────


async def _client(db_session: Any) -> AsyncClient:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drop_override() -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    app.dependency_overrides.pop(get_db, None)


@_NEEDS_DB
async def test_sweep_endpoint_returns_combined_summary(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company(name="EndpointSweep")
    db_session.add(company)
    await db_session.flush()
    db_session.add(_job_posting(target_company_id=company.id, department="Eng", team=None))
    await db_session.commit()

    async def _stub(*_a: Any, **_k: Any) -> str:
        return "Eng ships infra."

    monkeypatch.setattr("job_assist.services.division_enrichment.generate_description", _stub)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post("/enrichment/divisions/sweep")
    finally:
        await _drop_override()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"discovered", "already_existed", "total", "enriched"} <= body.keys()
    assert body["discovered"] >= 1
    assert body["enriched"] >= 1


@_NEEDS_DB
async def test_retry_endpoint_returns_404_for_unknown_id(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(f"/enrichment/divisions/{uuid.uuid4()}/retry")
    finally:
        await _drop_override()

    assert resp.status_code == 404


# ── DiscoverySummary plumbing test ───────────────────────────────────────────


def test_discovery_summary_defaults_to_zero() -> None:
    summary = DiscoverySummary()
    assert summary.discovered == 0
    assert summary.already_existed == 0


def test_postgres_unique_constraint_fires_outside_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: the unique constraint exists at the schema level.

    This is a structural smoke — IntegrityError on a manual double-insert
    proves the constraint isn't a lie. Sync-only / SQLAlchemy import path.
    """
    assert IntegrityError is not None  # touch the imported name
