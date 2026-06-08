"""Tests for the concurrency-safe sweep claim (feat/sweep-skip-locked).

- ``test_claim_*_compiles_*`` are pure (no DB): they capture the statement the
  helper builds and assert it carries ``FOR UPDATE SKIP LOCKED`` (+ the ``seen``
  exclusion), so a refactor can't silently drop the lock clause.
- ``test_concurrent_*`` are DB-gated: a SECOND connection locks a row, then the
  helper must SKIP it — the actual overlap-safety guarantee.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from job_assist.db.enums import RoleFamily, SeniorityLevel
from job_assist.db.models.job_posting import JobPosting
from job_assist.services.sweep_claim import claim_next_id

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure: the helper builds a FOR UPDATE SKIP LOCKED statement ────────────────


class _FakeResult:
    def scalars(self) -> _FakeResult:
        return self

    def first(self) -> None:
        return None


class _CapturingSession:
    """Records the last statement executed (no DB)."""

    def __init__(self) -> None:
        self.last: Any = None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.last = stmt
        return _FakeResult()


def _compiled(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_claim_compiles_for_update_skip_locked() -> None:
    base = select(JobPosting.id).where(JobPosting.closed_at.is_(None))
    sess = _CapturingSession()
    await claim_next_id(sess, base, JobPosting.id, set())  # type: ignore[arg-type]

    sql = _compiled(sess.last).upper()
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_claim_excludes_seen_ids() -> None:
    base = select(JobPosting.id).where(JobPosting.closed_at.is_(None))
    sess = _CapturingSession()
    seen = {uuid.uuid4()}
    await claim_next_id(sess, base, JobPosting.id, seen)  # type: ignore[arg-type]

    sql = _compiled(sess.last).upper()
    assert "NOT IN" in sql  # the seen-exclusion filter is applied
    assert "SKIP LOCKED" in sql


# ── DB-gated: a row locked by a peer transaction is skipped ───────────────────


def _posting(title: str, *, seen_at: datetime) -> JobPosting:
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="ClaimCo",
        target_company_id=None,
        normalized_title=title.lower(),
        raw_title=title,
        jd_text="JD body.",
        jd_text_hash=f"{'0' * 54}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.senior_pm.value,
        remote_type="remote",
        salary_min=150_000,
        salary_max=200_000,
        salary_currency="USD",
        salary_period="annual",
        locations_normalized=[{"remote_type": "remote", "city": "Remote"}],
        fit_score=90,
        jd_summary_markdown="## Role",
    )


_BASE = (
    select(JobPosting.id)
    .where(JobPosting.closed_at.is_(None))
    .where(JobPosting.canonical_company_name == "ClaimCo")
    .order_by(JobPosting.first_seen_at.asc())
)


async def _second_engine_conn() -> Any:
    """A SEPARATE connection to the same DB (independent transaction)."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    conn = await engine.connect()
    return engine, conn


@_NEEDS_DB
@pytest.mark.asyncio
async def test_concurrent_claim_skips_locked_row(db_session: Any) -> None:
    """A row locked by another connection is invisibly skipped — the helper
    returns the next FREE row instead, never the locked one."""
    now = datetime.now(tz=UTC)
    a = _posting("Role A", seen_at=now)
    b = _posting("Role B", seen_at=now)
    db_session.add_all([a, b])
    await db_session.commit()
    a_id, b_id = a.id, b.id

    engine, conn = await _second_engine_conn()
    trans = await conn.begin()
    try:
        # Peer run locks A (and holds the lock — no commit).
        locked = (
            await conn.execute(
                text("SELECT id FROM job_posting WHERE id = :id FOR UPDATE"),
                {"id": a_id},
            )
        ).scalar_one()
        assert locked == a_id

        # Our claim must skip the locked A and return the free B.
        got = await claim_next_id(db_session, _BASE, JobPosting.id, set())
        assert got == b_id
        assert got != a_id
        await db_session.commit()  # release our claim lock on B
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_concurrent_claim_returns_none_when_all_locked(db_session: Any) -> None:
    """When every eligible row is locked by a peer, the claim returns None
    (this run drains nothing now; the peer/next run handles them)."""
    now = datetime.now(tz=UTC)
    a = _posting("Role A", seen_at=now)
    b = _posting("Role B", seen_at=now)
    db_session.add_all([a, b])
    await db_session.commit()

    engine, conn = await _second_engine_conn()
    trans = await conn.begin()
    try:
        await conn.execute(
            text("SELECT id FROM job_posting WHERE canonical_company_name = 'ClaimCo' FOR UPDATE")
        )
        got = await claim_next_id(db_session, _BASE, JobPosting.id, set())
        assert got is None
        await db_session.rollback()
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()
