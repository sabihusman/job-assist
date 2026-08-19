"""Async Gmail backfill worker (D-SETTINGS-REWIRE Part C).

Job-row + poll pattern copied from ``services/reclassify_sweep``
(D-ASYNC-RESWEEP): the endpoint pre-inserts the job row, returns 202
immediately, and this in-process worker runs the backfill and finalizes the
row. The job row IS the existing ``gmail_sweep_run`` audit row
(kind='backfill') — the sync endpoint already wrote exactly one such row per
backfill via ``record_sweep``, so reusing it keeps one source of truth for
sweep state and needs no new table. ``record_sweep`` itself is untouched (the
poll path still uses it); this worker manages its pre-created row directly.

The runtime (GmailClient + EmailClassifier) is built at ENQUEUE time and
passed in, so this module never imports from ``main`` and a bad-credentials
construction error still surfaces as an HTTP error on the POST.

Single-worker deployment (scripts/start.sh, no ``--workers``) — the same
load-bearing assumption as ``gmail_sweep_slot``: in-process fire-and-forget
is process-global mutual exclusion here. A process death between 202 and
finalize leaves the row 'running' forever — the same exposure
``record_sweep`` already has; POST again for a fresh run.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from job_assist.db.models.gmail_sweep_run import GmailSweepRun
from job_assist.gmail.sweep_lock import GmailSweepBusyError, gmail_sweep_slot

logger = logging.getLogger("job_assist.services.gmail_backfill_job")

# The event loop holds only weak references to tasks, so a fire-and-forget
# create_task with no anchor can be garbage-collected mid-run (same guard as
# reclassify_sweep._RUNNING_JOBS).
_RUNNING_JOBS: set[asyncio.Task[None]] = set()


def spawn_gmail_backfill_job(
    job_id: uuid.UUID,
    *,
    gmail: Any,
    classifier: Any,
    days: int,
) -> None:
    """Fire-and-forget the worker for *job_id* on the running event loop."""
    task = asyncio.create_task(
        run_gmail_backfill_job(job_id, gmail=gmail, classifier=classifier, days=days)
    )
    _RUNNING_JOBS.add(task)
    task.add_done_callback(_RUNNING_JOBS.discard)


async def _finalize_job(
    job_id: uuid.UUID,
    *,
    status: str,
    report: Any | None = None,
    error: str | None = None,
) -> None:
    """Write the terminal state onto the pre-created gmail_sweep_run row."""
    from job_assist.db.session import _session_factory

    async with _session_factory() as session:
        row = await session.get(GmailSweepRun, job_id)
        if row is None:
            logger.warning("gmail_backfill_job.row_missing", extra={"job_id": str(job_id)})
            return
        row.status = status
        row.finished_at = datetime.now(tz=UTC)
        if report is not None:
            # Same count mapping as gmail_sweep_run.SweepHandle.set_counts.
            row.messages_listed = int(getattr(report, "message_ids_listed", 0) or 0)
            row.outcomes_inserted = int(getattr(report, "outcome_events_inserted", 0) or 0)
        if error:
            row.error_message = error[:500]
        await session.commit()


async def run_gmail_backfill_job(
    job_id: uuid.UUID,
    *,
    gmail: Any,
    classifier: Any,
    days: int,
) -> None:
    """Worker: hold the sweep slot, run the backfill, finalize the row.

    Never raises — every outcome lands on the job row. Opens its OWN sessions
    (the request's session is long gone by the time this runs).
    """
    from job_assist.db.session import _session_factory
    from job_assist.gmail.backfill import run_backfill

    try:
        # Same mutual exclusion as the old sync endpoint. The enqueue-time 409
        # already peeked at the lock; this re-check closes the race where a
        # poll grabbed the slot between the 202 and the task starting.
        async with gmail_sweep_slot(), _session_factory() as session:
            report = await run_backfill(session, gmail, classifier, days_back=days)
            # Gmail crawl tail (same hook the sync endpoint ran): reflect
            # newly-crawled applications in Companies. Best-effort — a
            # failure here must never fail the backfill itself.
            try:
                from job_assist.services.applied_companies import sync_applied_companies

                await sync_applied_companies(session)
            except Exception as exc:
                await session.rollback()
                logger.warning(
                    "applied_companies.sync_hook_failed", extra={"error": str(exc)[:300]}
                )
        await _finalize_job(job_id, status="success", report=report)
    except GmailSweepBusyError:
        await _finalize_job(
            job_id,
            status="failed",
            error=(
                "a Gmail sweep (poll or backfill) was already running when the job "
                "started — retry when it finishes"
            ),
        )
    except Exception as exc:
        logger.exception("gmail_backfill_job.failed", extra={"job_id": str(job_id)})
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        await _finalize_job(job_id, status="failed", error=first_line)


__all__ = ["run_gmail_backfill_job", "spawn_gmail_backfill_job"]
