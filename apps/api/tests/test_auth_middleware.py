"""Auth middleware tests (feat/api-auth).

The middleware gates every route except /health behind a shared bearer token,
with a WARN-only mode (log, allow) that flips to ENFORCE (401) via
``settings.auth_enforce``. These tests exercise both modes plus the carve-outs
(/health always open, CORS preflight OPTIONS always allowed, unconfigured token
fails OPEN rather than bricking).

No DB needed: /health and /openapi.json are both DB-free, so these run without
TEST_DATABASE_URL.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.config import settings
from job_assist.main import app

# A route that is gated (NOT in the allowlist) and needs no database.
_GATED_PATH = "/openapi.json"
_TOKEN = "test-secret-token-abc123"


@pytest.fixture
def restore_auth_settings() -> Iterator[None]:
    """Save/restore the mutable auth settings the middleware reads per-request."""
    orig_token = settings.api_auth_token
    orig_enforce = settings.auth_enforce
    try:
        yield
    finally:
        settings.api_auth_token = orig_token
        settings.auth_enforce = orig_enforce


async def _get(path: str, headers: dict[str, str] | None = None) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(path, headers=headers or {})
    return resp.status_code


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── /health is always open ────────────────────────────────────────────────────


async def test_health_open_even_when_enforcing(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = True
    # No Authorization header, enforce on — /health must still answer 200.
    assert await _get("/health") == 200


# ── WARN mode: log but allow ──────────────────────────────────────────────────


async def test_warn_mode_allows_missing_token(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = False
    # Gated route, no token, WARN mode → passes through (lets clients get wired).
    assert await _get(_GATED_PATH) == 200


async def test_warn_mode_allows_wrong_token(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = False
    assert await _get(_GATED_PATH, _bearer("wrong")) == 200


# ── ENFORCE mode: 401 on missing/invalid, 200 on correct ──────────────────────


async def test_enforce_rejects_missing_token(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = True
    assert await _get(_GATED_PATH) == 401


async def test_enforce_rejects_wrong_token(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = True
    assert await _get(_GATED_PATH, _bearer("not-the-token")) == 401


async def test_enforce_allows_correct_token(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = True
    assert await _get(_GATED_PATH, _bearer(_TOKEN)) == 200


# ── Misconfiguration: enforce on but no token → fail OPEN (don't brick) ────────


async def test_enforce_without_configured_token_fails_open(restore_auth_settings: None) -> None:
    settings.api_auth_token = ""  # unconfigured
    settings.auth_enforce = True
    # Must NOT 401 — a missing token env var should not brick every client.
    assert await _get(_GATED_PATH) == 200


# ── CORS preflight (OPTIONS) is never gated ───────────────────────────────────


async def test_options_preflight_not_blocked_by_auth(restore_auth_settings: None) -> None:
    settings.api_auth_token = _TOKEN
    settings.auth_enforce = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.options(
            _GATED_PATH,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    # The auth gate must not 401 a preflight (CORS answers it).
    assert resp.status_code != 401
