"""Tests for the startup schema guard (feat/migration-deploy-gate).

The rule-4 guard that would have caught #104 and #107: if the live DB schema is
behind the code's migration head, the app must REFUSE TO SERVE (raise on
startup) rather than return 500s. We prove both the comparison logic and that
the FastAPI lifespan actually aborts startup when behind.

Most tests need no DB — the revision fetch is monkeypatched so the guard's
decision logic is exercised directly. One DB-gated test runs the real fetch
against the migrated test DB.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from job_assist.db.schema_guard import (
    SchemaBehindError,
    assert_schema_at_head,
    check_revision,
    code_heads,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure comparison logic (no DB) ────────────────────────────────────────────


def test_check_revision_passes_when_current_is_head() -> None:
    check_revision("abc123", {"abc123"})  # no raise


def test_check_revision_raises_when_behind() -> None:
    with pytest.raises(SchemaBehindError, match="behind"):
        check_revision("oldrev", {"newhead"})


def test_check_revision_raises_when_unmigrated_none() -> None:
    """No alembic_version row (current=None) → never migrated → behind."""
    with pytest.raises(SchemaBehindError):
        check_revision(None, {"newhead"})


def test_code_heads_reads_single_head_from_migration_scripts() -> None:
    """code_heads() reads the in-repo migrations dir (no DB) and returns the
    single current head — the single-head rule in action."""
    heads = code_heads()
    assert len(heads) == 1, f"expected a single migration head, got {heads}"


# ── assert_schema_at_head with the fetch monkeypatched (no DB) ────────────────


async def test_assert_raises_when_db_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_old(_engine: Any) -> str:
        return "0000_behind_revision"

    monkeypatch.setattr("job_assist.db.schema_guard.db_current_revision", _fake_old)
    with pytest.raises(SchemaBehindError):
        await assert_schema_at_head(engine=None)  # type: ignore[arg-type]


async def test_assert_passes_when_db_at_head(monkeypatch: pytest.MonkeyPatch) -> None:
    head = next(iter(code_heads()))

    async def _fake_head(_engine: Any) -> str:
        return head

    monkeypatch.setattr("job_assist.db.schema_guard.db_current_revision", _fake_head)
    await assert_schema_at_head(engine=None)  # type: ignore[arg-type]  # no raise


# ── Lifespan integration: the "refuses to serve" proof ───────────────────────


async def test_lifespan_aborts_startup_when_schema_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the guard forced on and the DB behind, entering the app lifespan
    must RAISE — i.e. uvicorn startup fails and the deploy aborts, instead of
    serving 500s. This is the exact #104/#107 guard."""
    from job_assist.main import app, lifespan

    monkeypatch.setenv("SCHEMA_GUARD", "strict")

    async def _fake_old(_engine: Any) -> str:
        return "0000_behind_revision"

    monkeypatch.setattr("job_assist.db.schema_guard.db_current_revision", _fake_old)

    with pytest.raises(SchemaBehindError):
        async with lifespan(app):
            pass  # pragma: no cover — must not reach serving


async def test_lifespan_starts_when_schema_at_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard on + DB at head → lifespan enters cleanly (app serves)."""
    from job_assist.main import app, lifespan

    monkeypatch.setenv("SCHEMA_GUARD", "strict")
    head = next(iter(code_heads()))

    async def _fake_head(_engine: Any) -> str:
        return head

    monkeypatch.setattr("job_assist.db.schema_guard.db_current_revision", _fake_head)

    entered = False
    async with lifespan(app):
        entered = True
    assert entered, "lifespan should enter (serve) when the schema is at head"


async def test_lifespan_skips_guard_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCHEMA_GUARD=off → guard skipped even with a behind DB (dev escape
    hatch). The fetch must not even be consulted."""
    from job_assist.main import app, lifespan

    monkeypatch.setenv("SCHEMA_GUARD", "off")

    called = False

    async def _should_not_run(_engine: Any) -> str:
        nonlocal called
        called = True
        return "0000_behind_revision"

    monkeypatch.setattr("job_assist.db.schema_guard.db_current_revision", _should_not_run)
    async with lifespan(app):
        pass
    assert called is False, "guard must not run when SCHEMA_GUARD=off"


# ── DB-gated: real fetch against the migrated test DB ────────────────────────


@_NEEDS_DB
async def test_assert_schema_at_head_passes_against_migrated_db() -> None:
    """End-to-end: the real revision fetch against the CI test DB (which
    conftest migrates to head) passes. Proves the fetch + compare wiring, not
    just the logic."""
    from job_assist.db.session import engine

    await assert_schema_at_head(engine)  # no raise — conftest ran upgrade head
