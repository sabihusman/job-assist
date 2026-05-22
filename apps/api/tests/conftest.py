"""Pytest configuration and shared fixtures.

DB integration fixtures
───────────────────────
If TEST_DATABASE_URL is set (e.g. in CI), Alembic migrations are applied
once per session and each integration test runs against a real Postgres.
Tests decorated with @_NEEDS_DB skip when the env var is absent.
"""

from __future__ import annotations

import os

# ── CRITICAL: pin DATABASE_URL before any job_assist.* import ─────────────────
# Both the FastAPI app (db/session.py) and Alembic env.py read
# `settings.database_url`, which is created once at config-module import.
# CI only sets TEST_DATABASE_URL, so we must mirror it into DATABASE_URL here,
# at conftest module-load time, before any test or fixture triggers a
# `from job_assist.config import settings` import.
_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
if _TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL

_ALEMBIC_URL = _TEST_DATABASE_URL.replace("+asyncpg", "") if _TEST_DATABASE_URL else ""

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

# ── CRITICAL: replace the app's pooled engine with a NullPool engine ─────────
# `job_assist.db.session` creates a module-level AsyncEngine with the default
# QueuePool at import time. When tests use ASGITransport(app=app), the app's
# get_db() dependency uses THIS engine — not the per-test db_session fixture.
# With pytest-asyncio's function-scoped loops, a connection pooled by test N
# (on loop L_N) gets handed to test N+1 (on loop L_{N+1}) → "Future attached
# to a different loop". We must rebuild the engine with NullPool BEFORE the
# first test imports main.py.
if _TEST_DATABASE_URL:
    import job_assist.db.session as _app_session  # noqa: E402

    _app_session.engine = create_async_engine(
        _TEST_DATABASE_URL, echo=False, poolclass=NullPool, pool_pre_ping=True
    )
    _app_session._session_factory = async_sessionmaker(
        _app_session.engine, class_=AsyncSession, expire_on_commit=False
    )

# ── Migration setup (runs once per session) ────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Apply Alembic migrations to the test DB before any tests run."""
    if not _ALEMBIC_URL:
        return

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _ALEMBIC_URL)
    command.upgrade(cfg, "head")


# ── Per-test DB session ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session(_apply_migrations: None) -> AsyncGenerator[AsyncSession, None]:
    """Yield a real AsyncSession; truncate all data tables after each test."""
    if not _TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")

    # NullPool prevents asyncpg connections from being reused across tests.
    # Without it, a pooled connection bound to test N's event loop would be
    # handed to test N+1 (on a different loop) → "attached to a different loop".
    engine = create_async_engine(_TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as session:
        yield session
        # Clean up after each test so state doesn't bleed across tests.
        await session.execute(
            sa.text(
                "TRUNCATE posting_action, posting_source, triage_result, "
                "outcome_event, application_state, job_posting, ingest_run, "
                "closed_channel, target_company, contact CASCADE"
            )
        )
        await session.commit()

    await engine.dispose()
