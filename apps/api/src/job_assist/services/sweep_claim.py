"""Concurrency-safe row claiming for cron sweeps (feat/sweep-skip-locked).

The Gemini-calling sweeps (embeddings, reclassify, the enrichment sweeps) select
a batch of eligible rows and process them one at a time. If two sweep runs
overlap — a delayed cron firing next to a manual trigger, or broad-ingest racing
the daily cron — they can both pick the SAME rows and double-call Gemini, wasting
rate limit and spend.

``SELECT ... FOR UPDATE SKIP LOCKED`` turns the table into a safe work queue: a
claimed row is locked for the life of the claiming transaction, so a concurrent
run's claim invisibly skips it and grabs the next free row instead. No row is
processed twice concurrently; every row is still processed eventually (by whichever
run claims it, or the next scheduled run).

Two patterns, depending on how a sweep commits:

* **Per-row commit** (embeddings, jd-summaries, companies, divisions): a bulk
  ``FOR UPDATE`` is useless because the first per-row ``commit()`` releases every
  lock the bulk SELECT took. Instead claim ONE row per iteration with
  :func:`claim_next_id` — the lock is then held through that row's Gemini call and
  released only when its own write commits.

* **Single end-of-loop commit** (reclassify, score sweep): the whole sweep is one
  transaction, so adding ``.with_for_update(skip_locked=True)`` to the candidate
  SELECT already holds the locks for the run's duration — no helper needed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession


async def claim_next_id(
    session: AsyncSession,
    base_select: Select[Any],
    pk_col: Any,
    seen: set[Any],
) -> Any | None:
    """Lock and return the next eligible row's primary key, or ``None``.

    ``base_select`` selects ``pk_col`` with the sweep's eligibility ``WHERE`` and
    ``ORDER BY`` (no ``LIMIT`` — this adds it). The returned row is locked with
    ``FOR UPDATE SKIP LOCKED`` for the life of the CURRENT transaction, so a
    concurrent sweep's claim skips it. The caller must process the row and
    ``commit()`` (releasing the lock) before claiming the next one.

    ``seen`` excludes rows already handled in this run: a transient-error row stays
    eligible (its vector/summary still isn't written), so without this exclusion a
    single run could re-claim and re-process — re-calling Gemini — the same failing
    row in a tight loop. The caller adds each claimed id to ``seen``.
    """
    stmt = base_select
    if seen:
        stmt = stmt.where(pk_col.notin_(seen))
    stmt = stmt.limit(1).with_for_update(skip_locked=True)
    return (await session.execute(stmt)).scalars().first()


__all__ = ["claim_next_id"]
