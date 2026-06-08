"""The global unhandled-exception handler must SURFACE the real error.

Before this, an unhandled exception (a failing DB write) returned a bare 500
with an empty body — undiagnosable. The handler now echoes the exception type +
message in the response and logs the traceback. Pure (no DB) — runs everywhere.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from job_assist.main import unhandled_exception_handler


def _req(method: str = "POST", path: str = "/admin/seed/target-companies") -> Any:
    # The handler only reads request.method and request.url.path.
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


@pytest.mark.asyncio
async def test_handler_echoes_error_type_and_message() -> None:
    exc = ValueError('could not extend file "base/16384/1259": No space left on device')
    resp = await unhandled_exception_handler(_req(), exc)

    assert resp.status_code == 500
    body = json.loads(bytes(resp.body))
    assert body["error_type"] == "ValueError"
    assert "No space left on device" in body["error"]
    assert body["detail"] == "Internal Server Error"


@pytest.mark.asyncio
async def test_handler_truncates_long_messages() -> None:
    resp = await unhandled_exception_handler(_req(), RuntimeError("x" * 5000))
    body = json.loads(bytes(resp.body))
    # error capped at 500 chars in the response so a giant message can't bloat it.
    assert len(body["error"]) <= 500
    assert body["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_handler_carries_real_db_error_classes() -> None:
    # Simulates the asyncpg/SQLAlchemy classes the operator's hypotheses map to —
    # the CLASS NAME + message is what decides the cause.
    for exc, needle in (
        (ConnectionError("connection refused"), "connection refused"),
        (PermissionError("cannot execute INSERT in a read-only transaction"), "read-only"),
    ):
        resp = await unhandled_exception_handler(_req(), exc)
        body = json.loads(bytes(resp.body))
        assert body["error_type"] == type(exc).__name__
        assert needle in body["error"]
