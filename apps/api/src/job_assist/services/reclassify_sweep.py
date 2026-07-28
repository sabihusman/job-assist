"""Async reclassify sweep — job-row + poll execution model (D-ASYNC-RESWEEP).

The per-row work here is MOVED VERBATIM from the old synchronous
``POST /admin/reclassify/sweep`` handler (PR #48 + PR #56 rescoring + the
fix(audit) same-version skip) — this module changes how the sweep RUNS, not
what it computes per row:

  * The endpoint inserts a ``reclassify_job`` row (status='queued'), calls
    ``spawn_reclassify_job``, and returns 202 immediately — no more client
    HTTP timeout killing a big resweep ~23 rows in.
  * ``run_reclassify_job`` processes server-side batches of ``_BATCH_SIZE``
    (50), committing per batch and bumping ``processed``/``changed``/
    ``updated_at`` on the job row after each batch, so progress is pollable
    via GET /admin/reclassify/jobs/{id} while the job runs.
  * RESUMPTION: remaining work is derived from the candidate DB query on
    every batch — never from an in-memory total. If the process dies
    mid-run, POSTing a new job picks up whatever is still unjudged (the
    only_unclassified WHERE clause + the same-version skip make the query
    self-describing about what's left).

Sessions come from ``job_assist.db.session._session_factory`` — imported
LAZILY inside the functions (the ``record_sweep`` precedent) so tests /
conftest can rebind the factory to the test database before first use.

Per-batch commits also mean the ``FOR UPDATE SKIP LOCKED`` row locks are
held for one batch (~50 rows) instead of the whole run — an improvement on
the old whole-run hold, with identical overlap semantics (a concurrent job
works a disjoint set instead of double-buying Gemini calls).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Text, cast, or_, select

from job_assist.db.models import JobPosting, ReclassifyJob
from job_assist.db.models.operator_profile import OperatorProfile
from job_assist.db.models.target_company import TargetCompany

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50

# Keep strong references to in-flight worker tasks — asyncio only holds weak
# references to tasks, so a fire-and-forget create_task with no anchor can be
# garbage-collected mid-run.
_RUNNING_JOBS: set[asyncio.Task[None]] = set()


def spawn_reclassify_job(job_id: uuid.UUID, *, only_unclassified: bool) -> None:
    """Fire-and-forget the worker for *job_id* on the running event loop."""
    task = asyncio.create_task(run_reclassify_job(job_id, only_unclassified=only_unclassified))
    _RUNNING_JOBS.add(task)
    task.add_done_callback(_RUNNING_JOBS.discard)


async def process_reclassify_batch(
    session: AsyncSession,
    *,
    batch_size: int,
    only_unclassified: bool,
) -> tuple[int, int, int]:
    """Classify + rescore up to *batch_size* candidate rows; commit; return
    ``(processed, changed, skipped)``.

    Candidate selection, per-row classification, error handling, and the
    post-classification rescoring pass are byte-equivalent to the old
    synchronous handler — see the module docstring.
    """
    from job_assist.services.applied_corpus import load_applied_basis
    from job_assist.services.classifier import (
        CLASSIFIER_VERSION,
        build_profile_context,
        classify_posting,
    )
    from job_assist.services.scoring import SCORER_VERSION, score_posting_decomposed

    # ── 1. Select candidates ──────────────────────────────────────────────
    # Skip stale/closed postings (Bestiary 5.18) — don't burn LLM calls
    # reclassifying postings removed from their ATS board.
    stmt = select(JobPosting).where(JobPosting.closed_at.is_(None))
    if only_unclassified:
        stmt = stmt.where(
            or_(
                cast(JobPosting.role_family, Text) == "other",
                cast(JobPosting.seniority_level, Text) == "unknown",
            )
        )
        # fix(audit): skip rows THIS classifier version already judged — an
        # LLM-confirmed 'other'/'unknown' must not be re-bought at the same
        # CLASSIFIER_VERSION. The health check's reclassify_pending mirrors
        # this clause. (Unchanged from the synchronous handler.)
        stmt = stmt.where(
            or_(
                JobPosting.classified_at.is_(None),
                JobPosting.classifier_version.is_(None),
                JobPosting.classifier_version != CLASSIFIER_VERSION,
            )
        )
    # Oldest classified_at first; NULLs sort first so never-LLM-classified
    # rows are processed before rows the sweep has already touched.
    # FOR UPDATE SKIP LOCKED: a concurrent sweep works a disjoint set.
    stmt = (
        stmt.order_by(
            JobPosting.classified_at.asc().nulls_first(),
            JobPosting.first_seen_at.asc(),
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return 0, 0, 0

    # PR #56: load the operator profile once per batch for the
    # post-classification rescoring pass. NULL profile = unseeded — skip
    # rescoring rather than fail the sweep.
    op_row = await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    operator_profile = op_row.scalar_one_or_none()

    # A3: applied-corpus basis, loaded once per batch (only when boost on).
    applied_basis = (
        await load_applied_basis(session)
        if operator_profile is not None and (operator_profile.applied_corpus_weight or 0) > 0
        else None
    )

    # slice 2b: operator's free-form targets as DISAMBIGUATION context.
    profile_context = (
        build_profile_context(
            operator_profile.looking_for_text,
            operator_profile.role_keywords,
        )
        if operator_profile is not None
        else None
    )

    processed = 0
    changed = 0
    skipped = 0

    for posting in rows:
        processed += 1
        old_family = str(posting.role_family)
        old_seniority = str(posting.seniority_level)

        try:
            new_family, new_seniority = await classify_posting(
                posting.jd_text or "",
                posting.normalized_title,
                profile_context=profile_context,
            )
        except Exception as exc:
            logger.warning(
                "reclassify_sweep.row_failed",
                extra={
                    "posting_id": str(posting.id),
                    "error": str(exc)[:300],
                },
            )
            skipped += 1
            continue

        posting.role_family = new_family  # type: ignore[assignment]
        posting.seniority_level = new_seniority  # type: ignore[assignment]
        posting.classifier_version = CLASSIFIER_VERSION
        posting.classified_at = datetime.now(tz=UTC)

        # PR #56: rescore after each successful classification. Defensive
        # try/except mirrors the ingest path — a scoring bug must not
        # cascade to fail the whole sweep.
        if operator_profile is not None:
            try:
                tier_value: int | None = None
                if posting.target_company_id is not None:
                    tier_row = await session.execute(
                        select(TargetCompany.tier).where(
                            TargetCompany.id == posting.target_company_id
                        )
                    )
                    tier_value = tier_row.scalar_one_or_none()
                _decomp = score_posting_decomposed(
                    posting,
                    operator_profile,
                    tier=tier_value,
                    applied_basis=applied_basis,
                )
                posting.fit_score = _decomp.final
                posting.score_components = _decomp.to_dict()
                posting.scored_at = datetime.now(tz=UTC)
                posting.scorer_version = SCORER_VERSION
            except Exception as exc:
                logger.warning(
                    "reclassify_sweep.scoring_failed",
                    extra={
                        "posting_id": str(posting.id),
                        "error": str(exc)[:300],
                    },
                )

        if new_family != old_family or new_seniority != old_seniority:
            changed += 1

    if processed > skipped:
        await session.commit()
    else:
        # All rows in the batch failed — release the SKIP LOCKED locks so a
        # retry (or concurrent job) can reach them again.
        await session.rollback()

    return processed, changed, skipped


async def _finalize_job(
    job_id: uuid.UUID,
    *,
    status: str,
    error: str | None = None,
) -> None:
    from job_assist.db.session import _session_factory

    async with _session_factory() as session:
        job = await session.get(ReclassifyJob, job_id)
        if job is None:
            return
        now = datetime.now(tz=UTC)
        job.status = status
        job.error = error
        job.updated_at = now
        job.finished_at = now
        await session.commit()


async def run_reclassify_job(job_id: uuid.UUID, *, only_unclassified: bool) -> None:
    """Worker: batches of ``_BATCH_SIZE`` until the requested_limit budget is
    spent or the candidate pool runs dry. Never raises — every outcome lands
    on the job row."""
    from job_assist.db.session import _session_factory

    try:
        async with _session_factory() as session:
            job = await session.get(ReclassifyJob, job_id)
            if job is None:
                logger.warning("reclassify_job.missing", extra={"job_id": str(job_id)})
                return
            job.status = "running"
            job.updated_at = datetime.now(tz=UTC)
            await session.commit()

        while True:
            async with _session_factory() as session:
                # Re-read the job row each batch: remaining budget is derived
                # from the DB, never an in-memory total (resumption contract).
                job = await session.get(ReclassifyJob, job_id)
                if job is None:
                    return
                remaining = job.requested_limit - job.processed
                if remaining <= 0:
                    break

                processed, changed, _skipped = await process_reclassify_batch(
                    session,
                    batch_size=min(_BATCH_SIZE, remaining),
                    only_unclassified=only_unclassified,
                )

                if processed:
                    job.processed += processed
                    job.changed += changed
                    job.updated_at = datetime.now(tz=UTC)
                    await session.commit()
                else:
                    # Candidate pool exhausted — done early.
                    break

        await _finalize_job(job_id, status="succeeded")
    except Exception as exc:
        logger.exception("reclassify_job.failed", extra={"job_id": str(job_id)})
        await _finalize_job(job_id, status="failed", error=str(exc)[:500])


__all__ = [
    "process_reclassify_batch",
    "run_reclassify_job",
    "spawn_reclassify_job",
]
