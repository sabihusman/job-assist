"""Mutual exclusion for the Gmail sweeps (fix/audit concurrent-sweep guard).

The backfill runs 5-10 minutes under the Gemini throttle; the poll fires every
15 minutes from a cron — so an operator-run backfill realistically overlaps a
poll over the same recent messages. Both runners snapshot the already-classified
ids ONCE before their loop, so each pays a Gemini call for every shared message
and the slower runner's batch commit then hits the unique
``email_message_id`` constraint — rolling back up to 24 legitimately-new rows
along with the duplicate and failing the whole sweep.

One non-blocking in-process lock closes this: the deployment is a single
uvicorn worker (scripts/start.sh — no ``--workers``), so process-level mutual
exclusion IS global mutual exclusion here. A second sweep does not queue — it
raises :class:`GmailSweepBusyError` immediately so the endpoint can 409 and
the 15-minute cron simply tries again next tick (the running sweep covers the
same window anyway).

If the deployment ever moves to multiple workers/replicas, this must become a
DB-level claim (advisory lock or a claimed sweep row) — the single-worker
assumption is load-bearing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_gmail_sweep_lock = asyncio.Lock()


class GmailSweepBusyError(Exception):
    """Another Gmail sweep (poll or backfill) is already running."""


@asynccontextmanager
async def gmail_sweep_slot() -> AsyncIterator[None]:
    """Hold the single Gmail-sweep slot for the duration of the block.

    Non-blocking: raises :class:`GmailSweepBusyError` immediately when the
    slot is taken, instead of queueing a second sweep behind the first.
    """
    if _gmail_sweep_lock.locked():
        raise GmailSweepBusyError("a Gmail sweep (poll or backfill) is already running")
    async with _gmail_sweep_lock:
        yield


__all__ = ["GmailSweepBusyError", "gmail_sweep_slot"]
