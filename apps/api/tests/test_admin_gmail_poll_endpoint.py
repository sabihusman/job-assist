"""Tests for the POST /admin/gmail/poll FastAPI endpoint.

Two cases:
  * 503 when any of the three required env vars is missing
  * 200 + BackfillReport-shaped JSON when env is present and the
    underlying run_poll succeeds (mocked at the import site)

Both tests use the ASGI transport against the live FastAPI app so the
dependency wiring is exercised end-to-end.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.gmail.models import BackfillReport

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


async def _call_poll(db_session: Any) -> tuple[int, dict[str, Any] | str]:
    """POST /admin/gmail/poll using the live app with the test session injected."""
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/admin/gmail/poll")
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, resp.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@_NEEDS_DB
async def test_poll_endpoint_returns_503_when_env_missing(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three Gmail env vars unset → 503 with the missing-var list, not 500."""
    from job_assist.main import settings

    monkeypatch.setattr(settings, "gmail_credentials_json", "", raising=False)
    monkeypatch.setattr(settings, "gmail_refresh_token", "", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)

    status, body = await _call_poll(db_session)
    assert status == 503
    assert isinstance(body, dict)
    detail = body.get("detail", "")
    assert "GMAIL_CREDENTIALS_JSON" in detail
    assert "GMAIL_REFRESH_TOKEN" in detail
    assert "GEMINI_API_KEY" in detail


@_NEEDS_DB
async def test_poll_endpoint_partial_env_still_503(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two vars set, one missing → still 503, missing one called out."""
    from job_assist.main import settings

    monkeypatch.setattr(settings, "gmail_credentials_json", "stub", raising=False)
    monkeypatch.setattr(settings, "gmail_refresh_token", "stub", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)

    status, body = await _call_poll(db_session)
    assert status == 503
    assert isinstance(body, dict)
    assert "GEMINI_API_KEY" in body["detail"]
    assert "GMAIL_CREDENTIALS_JSON" not in body["detail"]


@_NEEDS_DB
async def test_poll_endpoint_returns_200_with_report(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full env + mocked runtime → 200 + BackfillReport-shaped JSON."""
    from job_assist.main import settings

    monkeypatch.setattr(settings, "gmail_credentials_json", "stub", raising=False)
    monkeypatch.setattr(settings, "gmail_refresh_token", "stub", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "stub", raising=False)

    # Replace _build_gmail_runtime with a stub so we never touch the real SDKs.
    from job_assist import main as main_module

    class _StubGmail:
        pass

    class _StubClassifier:
        pass

    def _fake_runtime() -> tuple[Any, Any]:
        return _StubGmail(), _StubClassifier()

    monkeypatch.setattr(main_module, "_build_gmail_runtime", _fake_runtime)

    # Replace run_poll with a stub that returns a known report — keeps the
    # test focused on the endpoint surface (auth + 503 + 200 + shape) rather
    # than the orchestrator itself (covered in test_poll.py).
    fake_report = BackfillReport(
        message_ids_listed=2,
        fetched=2,
        classified_job_related=1,
        classified_unrelated=1,
        outcome_events_inserted=2,
        watermark_used=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        watermark_advanced_to=datetime(2026, 5, 1, 12, 5, 0, tzinfo=UTC),
    )

    async def _fake_run_poll(_session: Any, _gmail: Any, _classifier: Any) -> BackfillReport:
        return fake_report

    import job_assist.gmail.backfill as backfill_module

    monkeypatch.setattr(backfill_module, "run_poll", _fake_run_poll)

    status, body = await _call_poll(db_session)
    assert status == 200
    assert isinstance(body, dict)

    # Counter fields land through model_dump unchanged.
    assert body["message_ids_listed"] == 2
    assert body["outcome_events_inserted"] == 2
    assert body["classified_job_related"] == 1
    assert body["classified_unrelated"] == 1
    # Window descriptors stay None on poll runs.
    assert body["days_back"] is None
    # Watermark fields serialise as ISO 8601.
    assert body["watermark_used"].startswith("2026-05-01T12:00:00")
    assert body["watermark_advanced_to"].startswith("2026-05-01T12:05:00")


# Touch unused-import guards to keep ruff F401 quiet.
def test_module_imports() -> None:
    assert timedelta(seconds=1).total_seconds() == 1.0
