"""Record Gmail sweep runs for the health monitor (feat/gmail-health-check).

The poll/backfill endpoints wrap their work in ``record_sweep`` so each sweep
leaves a ``gmail_sweep_run`` row with its start, finish, status, and counts. The
record is written from an ISOLATED session (its own ``_session_factory()``), NOT
the request's ``db`` — so:

  * the "started" row commits immediately and survives even if the sweep then
    raises and the request session rolls back, and
  * marking the row failed/succeeded never interferes with the sweep's own
    transaction.

Usage::

    async with record_sweep("poll") as sweep:
        report = await run_poll(db, gmail, classifier)
        sweep.set_counts(report)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from job_assist.db.models import GmailSweepRun

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class SweepHandle:
    """Mutable handle the caller uses to attach counts to the in-flight run."""

    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        self.messages_listed = 0
        self.outcomes_inserted = 0

    def set_counts(self, report: Any) -> None:
        """Pull the counters off a ``BackfillReport`` (poll or backfill)."""
        self.messages_listed = int(getattr(report, "message_ids_listed", 0) or 0)
        self.outcomes_inserted = int(getattr(report, "outcome_events_inserted", 0) or 0)


@asynccontextmanager
async def record_sweep(kind: str) -> AsyncIterator[SweepHandle]:
    """Persist a Gmail sweep run around the wrapped block.

    On clean exit the row is marked ``success`` with the handle's counts; on an
    exception it is marked ``failed`` with the error message, and the exception
    is re-raised unchanged.
    """
    from job_assist.db.session import _session_factory

    run_id = uuid.uuid4()
    async with _session_factory() as session:
        session.add(GmailSweepRun(id=run_id, kind=kind, status="running"))
        await session.commit()

    handle = SweepHandle(run_id)
    try:
        yield handle
    except BaseException as exc:  # finalize then re-raise unchanged
        await _finalize(run_id, status="failed", error=str(exc)[:500])
        raise
    else:
        await _finalize(
            run_id,
            status="success",
            messages_listed=handle.messages_listed,
            outcomes_inserted=handle.outcomes_inserted,
        )


async def _finalize(
    run_id: uuid.UUID,
    *,
    status: str,
    messages_listed: int = 0,
    outcomes_inserted: int = 0,
    error: str | None = None,
) -> None:
    from job_assist.db.session import _session_factory

    async with _session_factory() as session:
        run = await session.get(GmailSweepRun, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(tz=UTC)
        run.messages_listed = messages_listed
        run.outcomes_inserted = outcomes_inserted
        run.error_message = error
        await session.commit()


__all__ = ["SweepHandle", "record_sweep"]
