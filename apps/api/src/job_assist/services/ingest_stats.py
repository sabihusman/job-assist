"""Read layer over the (previously write-only) ``ingest_run`` audit table.

feat/ingest-visibility: ``ingest_run`` has been logging per-run counters since
PR #63 but nothing ever read it — the operator had no way to see whether the
daily ingest cron is actually landing postings. These are pure SELECTs (no new
table, no writes) that power the Stats-page ingest panel:

  * ``recent_runs`` — the latest rows, newest first (the audit log view).
  * ``ingest_daily_stats`` — daily SUM(postings_new) over a window, per-source
    last status, and success/fail totals (the glanceable "is it working" view).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models.ingest_run import IngestRun


def _enum(value: Any) -> Any:
    """Coerce an ATS / IngestRunStatus enum to its string value (tests may pass
    plain strings that SQLAlchemy hasn't coerced yet)."""
    return value.value if value is not None and hasattr(value, "value") else value


def _run_dict(r: IngestRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "source": _enum(r.source),
        "status": _enum(r.status),
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at is not None else None,
        "postings_fetched": r.postings_fetched,
        "postings_new": r.postings_new,
        "postings_updated": r.postings_updated,
        "error_message": r.error_message,
    }


async def recent_runs(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The most recent ingest_run rows, newest first. Optional ``since`` floor."""
    stmt = select(IngestRun).order_by(IngestRun.started_at.desc()).limit(limit)
    if since is not None:
        stmt = stmt.where(IngestRun.started_at >= since)
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_dict(r) for r in rows]


async def ingest_daily_stats(session: AsyncSession, *, days: int = 14) -> dict[str, Any]:
    """Daily new-posting counts + per-source last status + window totals.

    All three are single GROUP BY / DISTINCT ON / aggregate SELECTs — no N+1.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    day = func.date_trunc("day", IngestRun.started_at)

    daily_rows = (
        await session.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(IngestRun.postings_new), 0).label("postings_new"),
                func.coalesce(func.sum(IngestRun.postings_fetched), 0).label("postings_fetched"),
                func.count().label("runs"),
                func.count().filter(IngestRun.status == "failed").label("failures"),
            )
            .where(IngestRun.started_at >= since)
            .group_by(day)
            .order_by(day.desc())
        )
    ).all()
    daily = [
        {
            "day": d.date().isoformat(),
            "postings_new": int(pn),
            "postings_fetched": int(pf),
            "runs": int(rn),
            "failures": int(fl),
        }
        for d, pn, pf, rn, fl in daily_rows
    ]

    # Per-source LAST run (PG DISTINCT ON source, newest first).
    last_rows = (
        await session.execute(
            select(
                IngestRun.source,
                IngestRun.status,
                IngestRun.started_at,
                IngestRun.postings_new,
            )
            .order_by(IngestRun.source, IngestRun.started_at.desc())
            .distinct(IngestRun.source)
        )
    ).all()
    by_source = [
        {
            "source": _enum(src),
            "status": _enum(st),
            "last_run_at": ts.isoformat(),
            "postings_new": int(pn or 0),
        }
        for src, st, ts, pn in last_rows
    ]

    totals = (
        await session.execute(
            select(
                func.count().label("runs"),
                func.count().filter(IngestRun.status == "success").label("successes"),
                func.count().filter(IngestRun.status == "failed").label("failures"),
                func.coalesce(func.sum(IngestRun.postings_new), 0).label("postings_new"),
            ).where(IngestRun.started_at >= since)
        )
    ).one()

    return {
        "window_days": days,
        "totals": {
            "runs": int(totals.runs),
            "successes": int(totals.successes),
            "failures": int(totals.failures),
            "postings_new": int(totals.postings_new),
        },
        "daily": daily,
        "by_source": by_source,
    }


__all__ = ["ingest_daily_stats", "recent_runs"]
