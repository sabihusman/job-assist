"""DB-gated tests for GET /admin/ingest/health (the dead-man's-switch verdict).

Pins each check → failure-mode mapping so the alert cron fires for the right
reasons: a cron that didn't run / failed, the broad set going stale, and
starvation (≈0 net-new roles). DB-gated; runs on CI's postgres service.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import DiscoveredHandle, IngestRun, JobPosting

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _run(*, status: str, finished_hours_ago: float | None, started_hours_ago: float) -> IngestRun:
    now = datetime.now(tz=UTC)
    return IngestRun(
        source="greenhouse",  # type: ignore[arg-type]
        started_at=now - timedelta(hours=started_hours_ago),
        finished_at=(
            None if finished_hours_ago is None else now - timedelta(hours=finished_hours_ago)
        ),
        status=status,  # type: ignore[arg-type]
    )


def _handle(*, last_ingested_hours_ago: float | None) -> DiscoveredHandle:
    now = datetime.now(tz=UTC)
    return DiscoveredHandle(
        ats="greenhouse",  # type: ignore[arg-type]
        handle=f"h-{uuid.uuid4().hex[:8]}",
        last_ingested_at=(
            None
            if last_ingested_hours_ago is None
            else now - timedelta(hours=last_ingested_hours_ago)
        ),
        active=True,
    )


def _posting(*, first_seen_days_ago: float) -> JobPosting:
    now = datetime.now(tz=UTC)
    seen = now - timedelta(days=first_seen_days_ago)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="HealthCo",
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        remote_type="remote",  # type: ignore[arg-type]
        role_family="product_management",  # type: ignore[arg-type]
        seniority_level="senior_pm",  # type: ignore[arg-type]
        jd_text="JD",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=seen,
        last_seen_at=seen,
    )


async def _health(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/admin/ingest/health")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _healthy_fixtures() -> list[Any]:
    """A fully-healthy world: a recent successful run, a fresh broad sweep, and
    net-new postings inside the starvation window."""
    return [
        _run(status="success", finished_hours_ago=2, started_hours_ago=2.1),
        _handle(last_ingested_hours_ago=2),
        _posting(first_seen_days_ago=0.5),
    ]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_healthy_world_reports_ok(db_session: Any) -> None:
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is True, h["problems"]
    assert h["problems"] == []
    assert all(h["checks"].values())


@_NEEDS_DB
@pytest.mark.asyncio
async def test_starvation_flags_not_ok(db_session: Any) -> None:
    """Recent run + fresh broad sweep, but NO net-new postings in the window."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=2, started_hours_ago=2.1))
    db_session.add(_handle(last_ingested_hours_ago=2))
    db_session.add(_posting(first_seen_days_ago=30))  # old → outside starvation window
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["checks"]["not_starved"] is False
    assert any("starvation" in p for p in h["problems"])


@_NEEDS_DB
@pytest.mark.asyncio
async def test_failed_run_flags_not_ok(db_session: Any) -> None:
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    db_session.add(_run(status="failed", finished_hours_ago=1, started_hours_ago=1.1))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["checks"]["no_hard_failures"] is False
    assert h["metrics"]["failed_runs_recent"] >= 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_stale_broad_sweep_flags_not_ok(db_session: Any) -> None:
    """Curated ran fine + fresh postings, but the broad sweep is 3 days stale."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=2, started_hours_ago=2.1))
    db_session.add(_handle(last_ingested_hours_ago=72))  # stale
    db_session.add(_posting(first_seen_days_ago=0.5))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["checks"]["broad_fresh"] is False


@_NEEDS_DB
@pytest.mark.asyncio
async def test_no_recent_success_flags_not_ok(db_session: Any) -> None:
    """Last success was 3 days ago → recent_success fails (cron stopped running)."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=72, started_hours_ago=72.1))
    db_session.add(_handle(last_ingested_hours_ago=2))
    db_session.add(_posting(first_seen_days_ago=0.5))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["checks"]["recent_success"] is False


@_NEEDS_DB
@pytest.mark.asyncio
async def test_handle_not_found_is_not_a_hard_failure(db_session: Any) -> None:
    """A stale board (handle_not_found) is surfaced in metrics but does NOT trip
    the no_hard_failures check — it's a data signal, not a cron failure."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    db_session.add(_run(status="handle_not_found", finished_hours_ago=1, started_hours_ago=1.1))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is True, h["problems"]
    assert h["checks"]["no_hard_failures"] is True
    assert h["metrics"]["handle_not_found_recent"] >= 1
