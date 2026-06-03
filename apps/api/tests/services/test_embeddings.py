"""Tests for the embedding service + endpoints (slice 1, feat/embeddings-slice1).

Mirrors ``test_jd_summary_enrichment.py``: pure-function tests for the text
selector + hash + counter, DB-gated tests for the six-status state machine,
sweep, retry, profile-embed hook, and the nearest-neighbour validation gate.
The Gemini ``embed_text`` call is monkeypatched so no test hits the real SDK.

NOTE (slice-1 contract): nothing here touches fit_score / score_posting — the
tests only assert vector population + the read-only nearest view.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from job_assist.db.models import JobPosting, OperatorProfile, TargetCompany
from job_assist.services.embeddings import (
    EmbeddingResult,
    SweepSummary,
    embed_one_posting,
    embed_profile_if_changed,
    nearest_postings,
    recalibrate_similarity,
    reset_attempts_and_retry,
    select_embedding_text,
    sweep_embeddings,
    text_hash,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

_DIM = 768
_LONG_JD = (
    "Senior Product Manager — Risk Platform. We're hiring a Senior PM to own "
    "fraud-detection signals. You'll partner with engineering and data science "
    "to ship ML-backed defenses against account takeover. 5+ years PM required."
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _vec(hot: int) -> list[float]:
    """A 768-dim unit vector with a single hot dimension — lets cosine tests
    set up exact similarities (same hot dim => sim 1.0; different => sim 0.0)."""
    v = [0.0] * _DIM
    v[hot] = 1.0
    return v


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
    jd_text: str = _LONG_JD,
    summary: str | None = None,
    attempt_count: int = 0,
    embedding: list[float] | None = None,
    hash_embedded: str | None = None,
    fit_score: int | None = None,
) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        jd_text=jd_text,
        jd_text_hash=f"jdhash-{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        jd_summary_markdown=summary,
        embedding_attempt_count=attempt_count,
        jd_embedding=embedding,
        jd_text_hash_embedded=hash_embedded,
        fit_score=fit_score,
    )


def _patch_embed(monkeypatch: pytest.MonkeyPatch, value_or_exc: Any, hot: int = 0) -> None:
    """Replace embed_text with a stub returning a fixed vector (or raising)."""

    async def _stub(*_args: Any, **_kwargs: Any) -> list[float]:
        if isinstance(value_or_exc, Exception):
            raise value_or_exc
        if isinstance(value_or_exc, list):
            return value_or_exc
        return _vec(hot)

    monkeypatch.setattr("job_assist.services.embeddings.embed_text", _stub)


async def _reset_profile(db_session: Any) -> None:
    """Clear the singleton profile's text + embedding so a test doesn't bleed
    into others (conftest TRUNCATE does not cover operator_profile)."""
    prof = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if prof is not None:
        prof.looking_for_text = ""
        prof.looking_for_embedding = None
        prof.looking_for_embedding_hash = None
        prof.looking_for_embedded_at = None
        await db_session.commit()


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_text_hash_is_stable_and_changes_with_input() -> None:
    assert text_hash("hello") == text_hash("hello")
    assert text_hash("hello") != text_hash("hello!")
    assert len(text_hash("x")) == 64


def test_l2_normalize_returns_unit_vector() -> None:
    """gemini-embedding-001 doesn't normalize sub-3072 dims; l2_normalize must
    produce a unit vector (||v|| == 1) and preserve direction."""
    import math

    from job_assist.services.embeddings import l2_normalize

    out = l2_normalize([3.0, 4.0])  # norm 5
    assert out == pytest.approx([0.6, 0.8])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, abs_tol=1e-9)


def test_l2_normalize_zero_vector_is_unchanged() -> None:
    """A zero vector has no direction — return it as-is (no div-by-zero)."""
    from job_assist.services.embeddings import l2_normalize

    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_select_embedding_text_prefers_summary() -> None:
    posting = SimpleNamespace(jd_summary_markdown="S" * 150, jd_text="J" * 200)
    result = select_embedding_text(posting)  # type: ignore[arg-type]
    assert result is not None
    text, source = result
    assert source == "summary"
    assert text.startswith("S")


def test_select_embedding_text_falls_back_to_jd_text() -> None:
    posting = SimpleNamespace(jd_summary_markdown=None, jd_text="J" * 200)
    result = select_embedding_text(posting)  # type: ignore[arg-type]
    assert result is not None
    text, source = result
    assert source == "jd_text"
    assert text.startswith("J")


def test_select_embedding_text_truncates_to_max() -> None:
    posting = SimpleNamespace(jd_summary_markdown=None, jd_text="J" * 5000)
    result = select_embedding_text(posting)  # type: ignore[arg-type]
    assert result is not None
    text, _ = result
    assert len(text) == 3000


def test_select_embedding_text_none_when_too_short() -> None:
    posting = SimpleNamespace(jd_summary_markdown="", jd_text="short")
    assert select_embedding_text(posting) is None  # type: ignore[arg-type]


def test_sweep_summary_record_classifies_each_status() -> None:
    summary = SweepSummary()
    summary.record(EmbeddingResult(status="embedded", posting_id="a"))
    summary.record(EmbeddingResult(status="skipped", posting_id="b"))
    summary.record(EmbeddingResult(status="exhausted", posting_id="c"))
    summary.record(EmbeddingResult(status="missing_context", posting_id="d"))
    summary.record(EmbeddingResult(status="error", posting_id="e", error="boom"))
    assert summary.total == 5
    assert summary.embedded == 1
    assert summary.skipped == 1
    assert summary.exhausted == 1
    assert summary.missing_context == 1
    assert summary.errors == 1
    assert summary.error_details[0]["posting_id"] == "e"


# ── DB-gated: state machine ──────────────────────────────────────────────────


@_NEEDS_DB
async def test_embed_one_skipped_when_fresh_vector_present(
    db_session: Any,
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, embedding=_vec(0))
    p.jd_text_hash_embedded = p.jd_text_hash  # fresh: hash matches
    db_session.add(p)
    await db_session.commit()

    # No monkeypatch — a "skipped" must not call the SDK.
    result = await embed_one_posting(db_session, p.id)
    assert result.status == "skipped"


@_NEEDS_DB
async def test_embed_one_reembeds_when_jd_hash_changed(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, embedding=_vec(0), hash_embedded="STALE")
    db_session.add(p)
    await db_session.commit()

    _patch_embed(monkeypatch, "vec", hot=5)
    result = await embed_one_posting(db_session, p.id)
    assert result.status == "embedded"

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == p.id))
    ).scalar_one()
    assert refreshed.jd_text_hash_embedded == refreshed.jd_text_hash
    assert refreshed.embedding_model_version is not None


@_NEEDS_DB
async def test_embed_one_exhausted_when_max_attempts(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, attempt_count=3)
    db_session.add(p)
    await db_session.commit()

    result = await embed_one_posting(db_session, p.id)
    assert result.status == "exhausted"


@_NEEDS_DB
async def test_embed_one_missing_context_when_no_text(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, jd_text="short", summary=None)
    db_session.add(p)
    await db_session.commit()

    result = await embed_one_posting(db_session, p.id)
    assert result.status == "missing_context"

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == p.id))
    ).scalar_one()
    assert refreshed.embedding_attempt_count == 1
    assert refreshed.embedding_error is not None


@_NEEDS_DB
async def test_embed_one_success_populates_vector(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id)
    db_session.add(p)
    await db_session.commit()

    _patch_embed(monkeypatch, "vec", hot=3)
    result = await embed_one_posting(db_session, p.id)
    assert result.status == "embedded"
    assert result.source == "jd_text"

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == p.id))
    ).scalar_one()
    assert refreshed.jd_embedding is not None
    assert len(list(refreshed.jd_embedding)) == _DIM
    assert refreshed.embedded_at is not None
    assert refreshed.embedded_source == "jd_text"
    assert refreshed.embedding_error is None


@_NEEDS_DB
async def test_embed_one_prefers_summary_source(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, summary="**Scope**: " + "x" * 150)
    db_session.add(p)
    await db_session.commit()

    _patch_embed(monkeypatch, "vec")
    result = await embed_one_posting(db_session, p.id)
    assert result.status == "embedded"
    assert result.source == "summary"


@_NEEDS_DB
async def test_embed_one_increments_attempts_on_error(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id)
    db_session.add(p)
    await db_session.commit()

    _patch_embed(monkeypatch, RuntimeError("rate limited"))
    result = await embed_one_posting(db_session, p.id)
    assert result.status == "error"
    assert result.error is not None and "rate limited" in result.error

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == p.id))
    ).scalar_one()
    assert refreshed.embedding_attempt_count == 1
    assert refreshed.jd_embedding is None


# ── DB-gated: sweep ──────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_sweep_only_sees_eligible_rows(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    fresh = _posting(target_company_id=company.id, embedding=_vec(0))
    fresh.jd_text_hash_embedded = fresh.jd_text_hash  # fresh => filtered out
    db_session.add_all(
        [
            _posting(target_company_id=company.id),  # eligible
            fresh,  # already embedded (fresh)
            _posting(target_company_id=company.id, attempt_count=99),  # exhausted
        ]
    )
    await db_session.commit()

    _patch_embed(monkeypatch, "vec")
    summary = await sweep_embeddings(db_session)
    assert summary.total == 1
    assert summary.embedded == 1


@_NEEDS_DB
async def test_sweep_respects_limit(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    db_session.add_all([_posting(target_company_id=company.id) for _ in range(5)])
    await db_session.commit()

    _patch_embed(monkeypatch, "vec")
    summary = await sweep_embeddings(db_session, limit=2)
    assert summary.total == 2
    assert summary.embedded == 2


@_NEEDS_DB
async def test_sweep_skips_closed_postings(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    closed = _posting(target_company_id=company.id)
    closed.closed_at = datetime.now(tz=UTC)
    db_session.add_all([_posting(target_company_id=company.id), closed])
    await db_session.commit()

    _patch_embed(monkeypatch, "vec")
    summary = await sweep_embeddings(db_session)
    assert summary.total == 1  # closed row filtered at SELECT


# ── DB-gated: retry ──────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_retry_resets_and_reembeds(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, attempt_count=3, embedding=_vec(0))
    db_session.add(p)
    await db_session.commit()

    _patch_embed(monkeypatch, "vec", hot=7)
    result = await reset_attempts_and_retry(db_session, p.id)
    assert result.status == "embedded"

    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == p.id))
    ).scalar_one()
    assert refreshed.embedding_attempt_count == 0
    assert refreshed.jd_embedding is not None


@_NEEDS_DB
async def test_retry_not_found(db_session: Any) -> None:
    result = await reset_attempts_and_retry(db_session, uuid.uuid4())
    assert result.status == "not_found"


# ── DB-gated: profile embed hook ─────────────────────────────────────────────


@_NEEDS_DB
async def test_profile_embed_on_change(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    prof = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one()
    prof.looking_for_text = "fintech PM roles where an MBA adds value"
    await db_session.commit()

    _patch_embed(monkeypatch, "vec", hot=2)
    changed = await embed_profile_if_changed(db_session)
    assert changed is True

    await db_session.refresh(prof)
    assert prof.looking_for_embedding is not None
    assert prof.looking_for_embedding_hash == text_hash(prof.looking_for_text)

    # Second call with identical text is a no-op (hash matches).
    changed_again = await embed_profile_if_changed(db_session)
    assert changed_again is False

    await _reset_profile(db_session)


@_NEEDS_DB
async def test_profile_embed_cleared_when_text_empty(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prof = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one()
    prof.looking_for_text = "something"
    prof.looking_for_embedding = _vec(0)
    prof.looking_for_embedding_hash = "old"
    await db_session.commit()

    # Now clear the text — embedding should be dropped, no SDK call.
    prof.looking_for_text = ""
    await db_session.commit()
    changed = await embed_profile_if_changed(db_session)
    assert changed is False

    await db_session.refresh(prof)
    assert prof.looking_for_embedding is None

    await _reset_profile(db_session)


# ── DB-gated: nearest validation gate ────────────────────────────────────────


@_NEEDS_DB
async def test_nearest_unavailable_without_profile_vector(db_session: Any) -> None:
    await _reset_profile(db_session)  # ensure profile vector is NULL
    out = await nearest_postings(db_session, n=5)
    assert out["available"] is False
    assert out["results"] == []


@_NEEDS_DB
async def test_nearest_orders_by_cosine_and_reports_spread(
    db_session: Any,
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    # A shares the profile's hot dim (cosine 1.0); B is orthogonal (0.0).
    a = _posting(target_company_id=company.id, embedding=_vec(0), fit_score=70)
    a.jd_text_hash_embedded = a.jd_text_hash
    a.embedded_source = "jd_text"
    b = _posting(target_company_id=company.id, embedding=_vec(1), fit_score=42)
    b.jd_text_hash_embedded = b.jd_text_hash
    b.embedded_source = "summary"
    db_session.add_all([a, b])

    prof = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one()
    prof.looking_for_text = "match A"
    prof.looking_for_embedding = _vec(0)
    prof.looking_for_embedding_hash = "h"
    await db_session.commit()

    out = await nearest_postings(db_session, n=10)
    assert out["available"] is True
    assert out["results"][0]["posting_id"] == str(a.id)
    assert out["results"][0]["cosine_sim"] == pytest.approx(1.0, abs=1e-3)
    assert out["results"][0]["fit_score"] == 70
    assert out["spread"]["embedded_count"] == 2
    assert out["spread"]["cosine_sim_max"] >= out["spread"]["cosine_sim_min"]

    await _reset_profile(db_session)


# ── DB-gated: migration columns exist ────────────────────────────────────────


@_NEEDS_DB
async def test_migration_adds_embedding_columns(db_session: Any) -> None:
    cols = (
        (
            await db_session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'job_posting' "
                    "AND column_name IN ('jd_embedding','embedded_at',"
                    "'embedding_model_version','jd_text_hash_embedded',"
                    "'embedded_source','embedding_error','embedding_attempt_count')"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(set(cols)) == 7

    prof_cols = (
        (
            await db_session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'operator_profile' "
                    "AND column_name LIKE 'looking_for_embed%'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {"looking_for_embedding", "looking_for_embedded_at"}.issubset(set(prof_cols))


# ── DB-gated: similarity calibration (slice 2a) ──────────────────────────────


def _emb_cos(cos: float) -> list[float]:
    """A 768-dim *unit* vector whose cosine to ``_vec(0)`` (the e0 axis) is
    exactly ``cos`` - lets calibration tests stage a tightly-compressed band of
    cosines (mirrors prod's 0.58-0.75) with deterministic ordering."""
    import math

    v = [0.0] * _DIM
    v[0] = cos
    v[1] = math.sqrt(max(0.0, 1.0 - cos * cos))
    return v


async def _set_profile_embedding(db_session: Any, vec: list[float], tag: str) -> None:
    prof = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one()
    prof.looking_for_text = f"profile-{tag}"
    prof.looking_for_embedding = vec
    prof.looking_for_embedding_hash = tag
    await db_session.commit()


@_NEEDS_DB
async def test_recalibrate_spreads_compressed_cosines_to_uniform_0_100(
    db_session: Any,
) -> None:
    """The hard design point: raw cosine is compressed (~0.58-0.75); after
    PERCENT_RANK calibration the similarity_score must span ~0..100, be
    monotonic in cosine, and tie equal cosines to equal scores."""
    company = _company()
    db_session.add(company)
    await db_session.flush()

    # Six embedded rows in a compressed band, with a deliberate tie at 0.66.
    cosines = [0.58, 0.62, 0.66, 0.66, 0.70, 0.75]
    rows: list[tuple[float, uuid.UUID]] = []
    for c in cosines:
        p = _posting(target_company_id=company.id, embedding=_emb_cos(c))
        db_session.add(p)
        await db_session.flush()
        rows.append((c, p.id))
    await _set_profile_embedding(db_session, _vec(0), "e0")

    out = await recalibrate_similarity(db_session)
    assert out == {"available": True, "calibrated": 6}

    scores: dict[uuid.UUID, int] = {}
    for _c, pid in rows:
        scores[pid] = (
            await db_session.execute(
                select(JobPosting.similarity_score).where(JobPosting.id == pid)
            )
        ).scalar_one()

    ordered = [scores[pid] for _c, pid in sorted(rows, key=lambda r: r[0])]
    # Uniform spread: lowest cosine -> 0, highest -> 100 (not the 58-75 band).
    assert ordered[0] == 0
    assert ordered[-1] == 100
    # Monotonic non-decreasing with cosine.
    assert ordered == sorted(ordered)
    # The two equal cosines (0.66) get identical calibrated scores.
    tie = [scores[pid] for c, pid in rows if c == 0.66]
    assert len(tie) == 2
    assert tie[0] == tie[1]

    await _reset_profile(db_session)


@_NEEDS_DB
async def test_recalibrate_noop_when_profile_unembedded(db_session: Any) -> None:
    await _reset_profile(db_session)  # looking_for_embedding is NULL
    out = await recalibrate_similarity(db_session)
    assert out == {"available": False, "calibrated": 0, "reason": "profile not embedded yet"}


@_NEEDS_DB
async def test_recalibrate_recomputes_on_profile_change(db_session: Any) -> None:
    """similarity_score is percentile-of-cosine-to-*profile*, so changing the
    profile vector must flip the ranking — this is what the PUT-hook recompute
    relies on."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    emb_a = _emb_cos(0.95)
    emb_b = _emb_cos(0.60)
    a = _posting(target_company_id=company.id, embedding=emb_a)
    b = _posting(target_company_id=company.id, embedding=emb_b)
    db_session.add_all([a, b])
    await db_session.flush()
    a_id, b_id = a.id, b.id

    async def _score(pid: uuid.UUID) -> int:
        return (
            await db_session.execute(
                select(JobPosting.similarity_score).where(JobPosting.id == pid)
            )
        ).scalar_one()

    # Profile = e0: A (cos .95) is nearer than B (cos .60) → A ranks top.
    await _set_profile_embedding(db_session, _vec(0), "e0")
    await recalibrate_similarity(db_session)
    assert await _score(a_id) > await _score(b_id)

    # Re-point the profile AT B's own vector → B is now nearest → B ranks top.
    await _set_profile_embedding(db_session, emb_b, "atB")
    await recalibrate_similarity(db_session)
    assert await _score(b_id) > await _score(a_id)

    await _reset_profile(db_session)
