"""Re-score open postings after a similarity recalibration (slice 2b).

The scorer's ``semantic_fit`` feature reads the precomputed
``job_posting.similarity_score``, which ``services/embeddings.recalibrate_
similarity`` recomputes when new vectors land (embedding sweep tail) or the
profile vector changes (profile-save hook). Recalibration updates
``similarity_score`` but NOT ``fit_score`` — this helper closes that gap: it
re-runs ``score_posting`` over every open posting so the semantic blend (and a
profile-text edit) lands in ``fit_score`` right away, with no manual score
sweep.

Best-effort by contract: callers wrap it so a re-score failure never fails the
embedding sweep or the profile save (the score is decoration, not load-bearing).
Commits once at the end.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from job_assist.db.models import JobPosting, OperatorProfile, TargetCompany
from job_assist.services.scoring import SCORER_VERSION, score_posting


async def rescore_open_postings(session: AsyncSession) -> int:
    """Re-score every open posting with the current profile + similarity_score.

    Returns the number of rows re-scored. No-op (returns 0) when the operator
    profile is unseeded. Per-row scoring failures are skipped (the score is
    decoration); the batch commits once at the end.
    """
    profile = (
        await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if profile is None:
        return 0

    # Tier comes from target_company via OUTER JOIN — postings without a matched
    # company get NULL tier (the scorer maps that to a neutral 50).
    #
    # Defer the heavy columns the scorer never reads: score_posting needs only
    # small structured fields + the ``similarity_score`` int. Loading the full
    # JD text + the 768-float JD vector + the JD summary for every open row
    # (1000s of rows) balloons instance memory and OOMs the worker — defer them
    # so the re-score payload stays tiny. Everything else loads (no N+1).
    rows = (
        await session.execute(
            select(JobPosting, TargetCompany.tier)
            .outerjoin(TargetCompany, JobPosting.target_company_id == TargetCompany.id)
            .where(JobPosting.closed_at.is_(None))
            .options(
                defer(JobPosting.jd_text),
                defer(JobPosting.jd_embedding),
                defer(JobPosting.jd_summary_markdown),
            )
        )
    ).all()

    now = datetime.now(tz=UTC)
    rescored = 0
    for posting, tier in rows:
        try:
            posting.fit_score = score_posting(posting, profile, tier=tier)
            posting.scorer_version = SCORER_VERSION
            posting.scored_at = now
            rescored += 1
        except Exception:
            # A per-row scoring failure must not abort the batch.
            continue

    await session.commit()
    return rescored
