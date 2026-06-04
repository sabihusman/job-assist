"""Tests for the ingest-visibility read layer (feat/ingest-visibility).

DB-gated, through the endpoint functions: recent rows are returned newest-first,
daily SUM(postings_new) aggregates correctly, and a failed run surfaces its
status + error_message. Dates are relative to now() so the window filter holds
regardless of the CI clock.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from job_assist.db.models.ingest_run import IngestRun
from job_assist.main import get_stats_ingest, list_ingest_runs

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _run(
    *,
    started_at: datetime,
    source: str = "greenhouse",
    status: str = "success",
    postings_new: int = 0,
    postings_fetched: int = 0,
    postings_updated: int = 0,
    finished_at: datetime | None = None,
    error_message: str | None = None,
) -> IngestRun:
    return IngestRun(
        id=uuid.uuid4(),
        source=source,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        finished_at=finished_at,
        postings_new=postings_new,
        postings_fetched=postings_fetched,
        postings_updated=postings_updated,
        error_message=error_message,
    )


@_NEEDS_DB
async def test_ingest_runs_returns_recent_newest_first(db_session: Any) -> None:
    now = datetime.now(tz=UTC)
    db_session.add_all(
        [
            _run(started_at=now - timedelta(hours=2), postings_new=3),
            _run(started_at=now, postings_new=5, postings_fetched=20, postings_updated=2),
        ]
    )
    await db_session.commit()

    resp = await list_ingest_runs(db_session)
    assert resp["total"] == 2
    assert resp["items"][0]["postings_new"] == 5  # newest first
    assert set(resp["items"][0]) == {
        "id",
        "source",
        "status",
        "started_at",
        "finished_at",
        "postings_fetched",
        "postings_new",
        "postings_updated",
        "error_message",
    }


@_NEEDS_DB
async def test_stats_ingest_aggregates_new_postings_per_day(db_session: Any) -> None:
    today = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    db_session.add_all(
        [
            _run(started_at=today, postings_new=3),
            _run(started_at=today + timedelta(hours=1), postings_new=2),  # same day -> 5
            _run(started_at=yesterday, postings_new=7),
        ]
    )
    await db_session.commit()

    resp = await get_stats_ingest(db_session, days=14)
    by_day = {r["day"]: r for r in resp["daily"]}
    assert by_day[today.date().isoformat()]["postings_new"] == 5
    assert by_day[yesterday.date().isoformat()]["postings_new"] == 7
    assert resp["totals"]["postings_new"] == 12
    assert resp["totals"]["runs"] == 3
    assert resp["window_days"] == 14


@_NEEDS_DB
async def test_failed_run_surfaces_status(db_session: Any) -> None:
    now = datetime.now(tz=UTC)
    db_session.add(
        _run(
            source="lever",
            status="failed",
            started_at=now,
            error_message="upstream 503",
        )
    )
    await db_session.commit()

    runs = await list_ingest_runs(db_session)
    assert runs["items"][0]["status"] == "failed"
    assert runs["items"][0]["error_message"] == "upstream 503"

    stats = await get_stats_ingest(db_session, days=14)
    assert stats["totals"]["failures"] >= 1
    by_source = {s["source"]: s for s in stats["by_source"]}
    assert by_source["lever"]["status"] == "failed"
