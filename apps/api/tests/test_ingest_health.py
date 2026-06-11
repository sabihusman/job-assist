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

from job_assist.db.models import DiscoveredHandle, GmailSweepRun, IngestRun, JobPosting

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


def _posting(
    *,
    first_seen_days_ago: float,
    classified_hours_ago: float | None = None,
    embedded_hours_ago: float | None = None,
    embedding_error: str | None = None,
    embedding_attempt_count: int = 0,
) -> JobPosting:
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
        classified_at=(
            None if classified_hours_ago is None else now - timedelta(hours=classified_hours_ago)
        ),
        embedded_at=(
            None if embedded_hours_ago is None else now - timedelta(hours=embedded_hours_ago)
        ),
        embedding_error=embedding_error,
        embedding_attempt_count=embedding_attempt_count,
    )


def _gmail_sweep(
    *,
    started_hours_ago: float,
    runtime_seconds: float = 30.0,
    status: str = "success",
    kind: str = "poll",
) -> GmailSweepRun:
    now = datetime.now(tz=UTC)
    started = now - timedelta(hours=started_hours_ago)
    finished = None if status == "running" else started + timedelta(seconds=runtime_seconds)
    return GmailSweepRun(
        kind=kind,
        started_at=started,
        finished_at=finished,
        status=status,
    )


async def _health(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/admin/ingest/health")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _healthy_fixtures() -> list[Any]:
    """A fully-healthy world: a recent successful run, a fresh broad sweep,
    net-new postings inside the starvation window, a recent classifier run
    (so the LLM check passes), and a recent successful Gmail sweep."""
    return [
        _run(status="success", finished_hours_ago=2, started_hours_ago=2.1),
        _handle(last_ingested_hours_ago=2),
        _posting(first_seen_days_ago=0.5, classified_hours_ago=2),
        _gmail_sweep(started_hours_ago=2, runtime_seconds=42.0),
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
    assert h["severity"] == "ok"
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
    assert h["severity"] == "degraded"  # starvation is a SOFT problem → yellow
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
    assert h["severity"] == "down"  # a failed run is a HARD problem → red
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
    assert h["severity"] == "degraded"  # broad stale is a SOFT problem → yellow
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
    assert h["severity"] == "down"  # cron stopped running is a HARD problem → red
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
    assert h["severity"] == "ok"
    assert h["checks"]["no_hard_failures"] is True
    assert h["metrics"]["handle_not_found_recent"] >= 1


# ── feat/llm-health ──────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_stale_classifier_flags_degraded(db_session: Any) -> None:
    """Ingest is healthy, but the classifier sweep last ran >24h ago → YELLOW."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=2, started_hours_ago=2.1))
    db_session.add(_handle(last_ingested_hours_ago=2))
    db_session.add(_posting(first_seen_days_ago=0.5, classified_hours_ago=30))  # >24h
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["severity"] == "degraded"  # stale LLM is a SOFT problem → yellow
    assert h["checks"]["llm_healthy"] is False
    assert any("classifier sweep" in p for p in h["problems"])


@_NEEDS_DB
@pytest.mark.asyncio
async def test_some_llm_errors_flag_degraded(db_session: Any) -> None:
    """A few exhausted embedding errors (LLM calls failing) → YELLOW."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())  # classifier fresh
    db_session.add(
        _posting(first_seen_days_ago=10, embedding_error="boom", embedding_attempt_count=5)
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["severity"] == "degraded"  # a few errors is SOFT → yellow
    assert h["checks"]["llm_healthy"] is False
    assert h["metrics"]["llm_exhausted_errors"] >= 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_many_llm_errors_flag_down(db_session: Any) -> None:
    """A large pile of exhausted embedding errors = a hard LLM outage → RED."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    for _ in range(25):  # >= _HEALTH_LLM_HARD_ERRORS
        db_session.add(
            _posting(first_seen_days_ago=10, embedding_error="boom", embedding_attempt_count=5)
        )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["severity"] == "down"  # severe LLM failure is HARD → red
    assert h["checks"]["llm_healthy"] is False
    assert h["metrics"]["llm_exhausted_errors"] >= 25


@_NEEDS_DB
@pytest.mark.asyncio
async def test_llm_last_used_is_most_recent_activity(db_session: Any) -> None:
    """llm_last_used_at = the most recent of classified_at / embedded_at."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())  # has a posting classified 2h ago
    db_session.add(_posting(first_seen_days_ago=1, embedded_hours_ago=1))  # embedded 1h ago (newer)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["metrics"]["llm_last_used_at"] is not None
    assert h["metrics"]["llm_last_embedded_at"] is not None
    # The embedding (1h ago) is more recent than the classification (2h ago).
    assert h["metrics"]["llm_last_used_at"] == h["metrics"]["llm_last_embedded_at"]


# ── feat/gmail-health-check ───────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_gmail_healthy_reports_runtime(db_session: Any) -> None:
    """A recent successful Gmail sweep → gmail_healthy True, runtime surfaced."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())  # includes a 2h-ago sweep, 42s runtime
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["checks"]["gmail_healthy"] is True
    assert h["metrics"]["gmail_last_sweep_status"] == "success"
    assert h["metrics"]["gmail_last_sweep_runtime_seconds"] == pytest.approx(42.0, abs=0.5)
    assert h["metrics"]["gmail_last_sweep_at"] is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_gmail_stale_flags_degraded(db_session: Any) -> None:
    """Everything else healthy, but the last Gmail sweep started >13h ago → YELLOW."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=2, started_hours_ago=2.1))
    db_session.add(_handle(last_ingested_hours_ago=2))
    db_session.add(_posting(first_seen_days_ago=0.5, classified_hours_ago=2))
    db_session.add(_gmail_sweep(started_hours_ago=20))  # > _HEALTH_GMAIL_STALE_HOURS
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["severity"] == "degraded"  # stale Gmail is a SOFT problem → yellow
    assert h["checks"]["gmail_healthy"] is False
    assert any("Gmail sweep has not run" in p for p in h["problems"])


@_NEEDS_DB
@pytest.mark.asyncio
async def test_gmail_no_sweep_ever_flags_degraded(db_session: Any) -> None:
    """No Gmail sweep has ever run → gmail_healthy False, runtime None."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=2, started_hours_ago=2.1))
    db_session.add(_handle(last_ingested_hours_ago=2))
    db_session.add(_posting(first_seen_days_ago=0.5, classified_hours_ago=2))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["checks"]["gmail_healthy"] is False
    assert h["severity"] == "degraded"
    assert h["metrics"]["gmail_last_sweep_at"] is None
    assert h["metrics"]["gmail_last_sweep_runtime_seconds"] is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_gmail_last_sweep_failed_flags_degraded(db_session: Any) -> None:
    """A recent but FAILED Gmail sweep → gmail_healthy False (soft/yellow)."""
    from job_assist.main import app

    db_session.add(_run(status="success", finished_hours_ago=2, started_hours_ago=2.1))
    db_session.add(_handle(last_ingested_hours_ago=2))
    db_session.add(_posting(first_seen_days_ago=0.5, classified_hours_ago=2))
    db_session.add(_gmail_sweep(started_hours_ago=1, status="failed"))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["severity"] == "degraded"
    assert h["checks"]["gmail_healthy"] is False
    assert h["metrics"]["gmail_last_sweep_status"] == "failed"
    assert any("last Gmail sweep failed" in p for p in h["problems"])


# ── feat/warm-path-ingest ─────────────────────────────────────────────────────


def _warm_company(*, swept_days_ago: float | None, name_suffix: str = "") -> Any:
    from job_assist.db.models import TargetCompany

    now = datetime.now(tz=UTC)
    return TargetCompany(
        name=f"WarmCo{name_suffix or uuid.uuid4().hex[:6]}",
        tier=None,
        ats="workday",  # type: ignore[arg-type]
        domain="warmco.com",
        source="warm_path",
        last_swept_at=(None if swept_days_ago is None else now - timedelta(days=swept_days_ago)),
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_warm_path_fresh_trivially_true_when_cohort_empty(db_session: Any) -> None:
    """No warm_path companies (pre-seeding) → the check passes; healthy world
    stays fully green."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["checks"]["warm_path_fresh"] is True
    assert h["metrics"]["warm_path_companies"] == 0
    assert h["metrics"]["warm_path_last_swept_at"] is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_warm_path_fresh_within_window(db_session: Any) -> None:
    """Cohort swept 2 days ago → fresh (weekly cadence, 9-day window)."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    db_session.add(_warm_company(swept_days_ago=2))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["checks"]["warm_path_fresh"] is True
    assert h["severity"] == "ok"
    assert h["metrics"]["warm_path_companies"] == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_warm_path_stale_flags_degraded(db_session: Any) -> None:
    """Cohort exists but last sweep started >9 days ago → SOFT/yellow."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    db_session.add(_warm_company(swept_days_ago=12))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["ok"] is False
    assert h["severity"] == "degraded"
    assert h["checks"]["warm_path_fresh"] is False
    assert any("warm-path sweep" in p for p in h["problems"])


@_NEEDS_DB
@pytest.mark.asyncio
async def test_warm_path_seeded_never_swept_flags_degraded(db_session: Any) -> None:
    """Cohort seeded but never swept (last_swept_at NULL everywhere) → degraded,
    so a never-armed weekly cron can't read green forever."""
    from job_assist.main import app

    db_session.add_all(_healthy_fixtures())
    db_session.add(_warm_company(swept_days_ago=None))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        h = await _health(client)

    assert h["checks"]["warm_path_fresh"] is False
    assert h["severity"] == "degraded"
