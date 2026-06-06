"""DB-gated tests for rescore_open_postings (slice 2b).

The re-score helper is what makes the semantic blend land: after
``recalibrate_similarity`` updates ``job_posting.similarity_score`` (embedding
sweep tail / profile-save hook), this re-runs ``score_posting`` over open rows
so ``fit_score`` reflects the new ``semantic_fit``. The scoring math itself is
covered by the pure tests in tests/services/test_scoring.py — here we lock the
loop mechanics: it reads the stored ``similarity_score`` and writes
``fit_score`` + ``scorer_version`` + ``scored_at``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from job_assist.db.models import JobPosting, TargetCompany
from job_assist.services.rescore import rescore_open_postings
from job_assist.services.scoring import SCORER_VERSION

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _posting(
    *,
    target_company_id: uuid.UUID,
    similarity_score: int | None = None,
    fit_score: int | None = None,
) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        jd_text="JD body.",
        jd_text_hash=f"{'0' * 56}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        role_family="product_management",  # type: ignore[arg-type]
        seniority_level="senior_pm",  # type: ignore[arg-type]
        fit_score=fit_score,
        similarity_score=similarity_score,
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rescore_updates_fit_score_version_and_timestamp(db_session: Any) -> None:
    """A row with a calibrated similarity_score gets re-scored: fit_score is
    written, stamped with the current SCORER_VERSION and scored_at.

    operator_profile id=1 is seeded by the test DB, so the helper proceeds.
    """
    tc = TargetCompany(
        name=f"Co-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"h-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id, similarity_score=90, fit_score=None)
    db_session.add(p)
    await db_session.commit()

    rescored = await rescore_open_postings(db_session)
    assert rescored >= 1

    await db_session.refresh(p)
    assert p.fit_score is not None
    assert p.scorer_version == SCORER_VERSION
    assert p.scored_at is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rescore_skips_closed_postings(db_session: Any) -> None:
    """Closed postings are not re-scored (they never surface in Triage)."""
    tc = TargetCompany(
        name=f"Co-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"h-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(tc)
    await db_session.flush()
    closed = _posting(target_company_id=tc.id, similarity_score=90, fit_score=None)
    closed.closed_at = datetime.now(tz=UTC)
    db_session.add(closed)
    await db_session.commit()

    await rescore_open_postings(db_session)
    await db_session.refresh(closed)
    assert closed.fit_score is None  # untouched
