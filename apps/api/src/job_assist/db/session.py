"""Async SQLAlchemy engine and session factory for FastAPI."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from job_assist.config import settings

# ``statement_cache_size=0`` is required when connecting through
# Supabase's Transaction-mode pooler (PgBouncer). The pooler rotates
# the backing PG connection between transactions, but asyncpg caches
# prepared statements by sequential name on the *driver* connection.
# When a recycled backend already has ``__asyncpg_stmt_N__`` from an
# earlier transaction, asyncpg's next attempt to prepare under the
# same name fails with ``DuplicatePreparedStatementError``.
#
# Surfaced incidents (resolved by this fix):
#   * 2026-06-02: ``GET /postings`` 500ed on every call once PR #58
#     introduced a per-call dynamic CTE. Hotfixed by defaulting the
#     cap to 0 (see ``hotfix/postings-pooler-incident``); proper fix
#     is this engine-level disable.
#   * 2026-06-02: ``POST /admin/gmail/poll`` 500ed with the same
#     ``DuplicatePreparedStatementError`` on the watermark SELECT.
#     No code-path change in PR #58 affected Gmail; the collision is
#     latent on any query whenever a pooled connection rotates.
#
# Trade-off: every query is re-parsed and re-planned per execution
# rather than cached on the driver. For this app's interactive
# throughput (single operator, ~10s of req/min) the cost is noise.
# If a future high-RPS code path needs caching, it should run
# against a *Session*-mode Supabase pool (no pooler rotation) or
# bypass the pool entirely.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0},
)

_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a managed async DB session."""
    async with _session_factory() as session:
        yield session
