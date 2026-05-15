"""Pytest configuration and shared fixtures.

DB integration fixtures
───────────────────────
If TEST_DATABASE_URL is set (e.g. in CI), Alembic migrations are applied
once per session and each integration test runs against a real Postgres.
Tests decorated with @_NEEDS_DB skip when the env var is absent.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
_ALEMBIC_URL = _TEST_DATABASE_URL.replace("+asyncpg", "") if _TEST_DATABASE_URL else ""


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

    engine = create_async_engine(_TEST_DATABASE_URL, echo=False)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as session:
        yield session
        # Clean up after each test so state doesn't bleed across tests.
        await session.execute(
            sa.text(
                "TRUNCATE posting_source, triage_result, outcome_event, "
                "application_state, job_posting, ingest_run, "
                "closed_channel, target_company CASCADE"
            )
        )
        await session.commit()

    await engine.dispose()
