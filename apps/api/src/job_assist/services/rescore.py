"""Re-score open postings after a similarity recalibration (slice 2b).

The scorer's ``semantic_fit`` feature reads the precomputed
``job_posting.similarity_score``, which ``services/embeddings.recalibrate_
similarity`` recomputes when new vectors land (embedding sweep tail) or the
profile vector changes (profile-save hook). Recalibration updates
``similarity_score`` but NOT ``fit_score`` — this helper closes that gap: it
re-runs ``score_posting`` over every open posting so the semantic blend (and a
profile-text edit) lands in ``fit_score`` right away, with no manual sweep.

FIXED MEMORY: the corpus is re-scored in ``batch_size`` passes, paginated by
posting id. Each pass loads only the small structured columns the scorer reads
(the heavy ``jd_text`` / ``jd_embedding`` / ``jd_summary_markdown`` are
deferred), commits, then expunges the batch from the identity map — so peak
memory is bounded by ``batch_size`` regardless of corpus size. This is the
single engine the embedding-sweep tail, the profile-save hook, and the backfill
endpoint all share, so none of them can OOM the instance (the unchunked
load-everything version did).

Best-effort by contract: callers wrap it so a re-score failure never fails the
embedding sweep or the profile save (the score is decoration, not load-bearing).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from job_assist.db.models import JobPosting, OperatorProfile, TargetCompany
from job_assist.services.scoring import SCORER_VERSION, score_posting_decomposed

# Rows loaded into memory per pass. Small enough that peak memory stays flat on
# a tiny instance; large enough that the pass count (and commit count) stays
# reasonable for a few-thousand-row corpus.
DEFAULT_RESCORE_BATCH_SIZE = 100


async def rescore_open_postings(
    session: AsyncSession,
    *,
    batch_size: int = DEFAULT_RESCORE_BATCH_SIZE,
) -> tuple[int, int]:
    """Re-score every open posting in fixed-memory chunks.

    Returns ``(rescored, changed)`` — total rows re-scored and how many had a
    different ``fit_score`` than before. No-op ``(0, 0)`` when the operator
    profile is unseeded. Per-row scoring failures are skipped (the score is
    decoration); each batch commits independently, so a mid-run failure still
    persists the work done so far.
    """
    profile = (
        await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if profile is None:
        return 0, 0

    # A3: applied-corpus basis, loaded ONCE for the whole rescore (only when the
    # boost is on). When on, the boost reads jd_embedding, so it must NOT be
    # deferred below.
    from job_assist.services.applied_corpus import load_applied_basis

    applied_basis = (
        await load_applied_basis(session) if (profile.applied_corpus_weight or 0) > 0 else None
    )

    # Lightweight: just the open posting ids (UUIDs are tiny even for 1000s of
    # rows). We page over these so we never hold more than ``batch_size`` full
    # rows in memory at once.
    ids = (
        (
            await session.execute(
                select(JobPosting.id).where(JobPosting.closed_at.is_(None)).order_by(JobPosting.id)
            )
        )
        .scalars()
        .all()
    )

    rescored = 0
    changed = 0
    for start in range(0, len(ids), batch_size):
        chunk_ids = ids[start : start + batch_size]
        # Tier via OUTER JOIN (NULL → neutral 50 in the scorer). Heavy columns
        # deferred — the scorer reads only small structured fields + the
        # similarity_score int.
        _defers = [defer(JobPosting.jd_text), defer(JobPosting.jd_summary_markdown)]
        if applied_basis is None:
            _defers.append(defer(JobPosting.jd_embedding))
        rows = (
            await session.execute(
                select(JobPosting, TargetCompany.tier)
                .outerjoin(TargetCompany, JobPosting.target_company_id == TargetCompany.id)
                .where(JobPosting.id.in_(chunk_ids))
                .options(*_defers)
            )
        ).all()

        now = datetime.now(tz=UTC)
        for posting, tier in rows:
            try:
                _decomp = score_posting_decomposed(
                    posting, profile, tier=tier, applied_basis=applied_basis
                )
                new_score = _decomp.final
            except Exception:
                # A per-row scoring failure must not abort the batch.
                continue
            if new_score != posting.fit_score:
                changed += 1
            posting.fit_score = new_score
            posting.score_components = _decomp.to_dict()
            posting.scorer_version = SCORER_VERSION
            posting.scored_at = now
            rescored += 1

        await session.commit()
        # Drop this batch's ORM objects from the identity map so memory doesn't
        # accumulate across passes. ``profile`` (expired by the commit) refreshes
        # cheaply on the next pass's first scorer read — a tiny single-row query,
        # not an N+1.
        for posting, _tier in rows:
            session.expunge(posting)

    return rescored, changed
