"""Tests for the async Gmail backfill (D-SETTINGS-REWIRE Part C).

The HTTP contract mirrors ``test_admin_reclassify.py``: POST → 202 {job_id,
status} + a persisted job row, GET → the row. The job row is the existing
``gmail_sweep_run`` audit row (kind='backfill') — no new table. The worker is
tested directly (``run_gmail_backfill_job``) with ``run_backfill``
monkeypatched so no test touches Gmail or Gemini; the endpoint tests no-op
the fire-and-forget spawn (same pattern as reclassify's ``_patch_spawn``).
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models.gmail_sweep_run import GmailSweepRun

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _patch_env(monkeypatch: pytest.MonkeyPatch, *, present: bool) -> None:
    """Set or clear the three required Gmail env settings."""
    from job_assist.main import settings

    value = "stub" if present else ""
    monkeypatch.setattr(settings, "gmail_credentials_json", value, raising=False)
    monkeypatch.setattr(settings, "gmail_refresh_token", value, raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", value, raising=False)


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub _build_gmail_runtime so no test constructs real SDK clients."""
    import job_assist.main as main_module

    monkeypatch.setattr(main_module, "_build_gmail_runtime", lambda: (object(), object()))


def _patch_spawn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """No-op the fire-and-forget spawn so endpoint tests stay deterministic."""
    spawned: list[dict[str, Any]] = []

    def _stub(job_id: uuid.UUID, *, gmail: Any, classifier: Any, days: int) -> None:
        spawned.append({"job_id": job_id, "days": days})

    monkeypatch.setattr(
        "job_assist.services.gmail_backfill_job.spawn_gmail_backfill_job",
        _stub,
    )
    return spawned


async def _client(db_session: Any) -> AsyncClient:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drop_override() -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    app.dependency_overrides.pop(get_db, None)


# ── Endpoint: POST /admin/gmail/backfill ──────────────────────────────────────


@_NEEDS_DB
async def test_post_backfill_returns_503_when_env_missing(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_env(monkeypatch, present=False)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post("/admin/gmail/backfill?days=60")
    finally:
        await _drop_override()

    assert resp.status_code == 503
    assert "missing env var" in resp.text


@_NEEDS_DB
async def test_post_backfill_returns_202_with_job_row(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_env(monkeypatch, present=True)
    _patch_runtime(monkeypatch)
    spawned = _patch_spawn(monkeypatch)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post("/admin/gmail/backfill?days=30")
    finally:
        await _drop_override()

    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "running"
    assert data["days_back"] == 30
    job_id = uuid.UUID(data["job_id"])

    row = await db_session.get(GmailSweepRun, job_id)
    assert row is not None
    assert row.kind == "backfill"
    assert row.status == "running"
    assert [s["job_id"] for s in spawned] == [job_id]
    assert spawned[0]["days"] == 30


@_NEEDS_DB
async def test_post_backfill_409_while_sweep_slot_held(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enqueue-time 409 fires while any sweep (poll or backfill) runs."""
    from job_assist.gmail.sweep_lock import gmail_sweep_slot

    _patch_env(monkeypatch, present=True)
    _patch_runtime(monkeypatch)
    _patch_spawn(monkeypatch)

    ac = await _client(db_session)
    try:
        async with ac, gmail_sweep_slot():
            resp = await ac.post("/admin/gmail/backfill?days=60")
    finally:
        await _drop_override()

    assert resp.status_code == 409
    assert "already running" in resp.text


# ── Endpoint: GET /admin/gmail/jobs/{job_id} ──────────────────────────────────


@_NEEDS_DB
async def test_get_job_returns_row_and_404_for_unknown(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = GmailSweepRun(kind="backfill", status="running")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    ac = await _client(db_session)
    try:
        async with ac:
            found = await ac.get(f"/admin/gmail/jobs/{row.id}")
            missing = await ac.get(f"/admin/gmail/jobs/{uuid.uuid4()}")
    finally:
        await _drop_override()

    assert found.status_code == 200
    body = found.json()
    assert body["job_id"] == str(row.id)
    assert body["kind"] == "backfill"
    assert body["status"] == "running"
    assert missing.status_code == 404


# ── Worker: run_gmail_backfill_job ────────────────────────────────────────────


async def _make_job_row(db_session: Any) -> GmailSweepRun:
    row = GmailSweepRun(kind="backfill", status="running")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@_NEEDS_DB
async def test_worker_finalizes_success_with_counts(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.gmail_backfill_job import run_gmail_backfill_job

    row = await _make_job_row(db_session)

    report = SimpleNamespace(message_ids_listed=42, outcome_events_inserted=7)

    async def _fake_backfill(session: Any, gmail: Any, classifier: Any, *, days_back: int) -> Any:
        assert days_back == 30
        return report

    monkeypatch.setattr("job_assist.gmail.backfill.run_backfill", _fake_backfill)

    # The Companies-sync tail is best-effort; stub it so the worker test does
    # not depend on that service's fixtures.
    async def _noop_sync(session: Any) -> None:
        return None

    monkeypatch.setattr("job_assist.services.applied_companies.sync_applied_companies", _noop_sync)

    await run_gmail_backfill_job(row.id, gmail=object(), classifier=object(), days=30)

    db_session.expire_all()  # worker committed in its own session
    fresh = await db_session.get(GmailSweepRun, row.id)
    assert fresh is not None
    assert fresh.status == "success"
    assert fresh.messages_listed == 42
    assert fresh.outcomes_inserted == 7
    assert fresh.finished_at is not None
    assert fresh.error_message is None


@_NEEDS_DB
async def test_worker_finalizes_failure_with_error_message(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.gmail_backfill_job import run_gmail_backfill_job

    row = await _make_job_row(db_session)

    async def _boom(session: Any, gmail: Any, classifier: Any, *, days_back: int) -> Any:
        raise RuntimeError("Gemini quota exhausted\nlong traceback line 2")

    monkeypatch.setattr("job_assist.gmail.backfill.run_backfill", _boom)

    await run_gmail_backfill_job(row.id, gmail=object(), classifier=object(), days=60)

    db_session.expire_all()
    fresh = await db_session.get(GmailSweepRun, row.id)
    assert fresh is not None
    assert fresh.status == "failed"
    # First line only — some Google errors embed full request URLs.
    assert fresh.error_message == "Gemini quota exhausted"
    assert fresh.finished_at is not None
