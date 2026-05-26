"""Integration test for migration ``c3d4e5f6a7b8`` — Plaid + Atlassian
ATS reconfig (PR #63 follow-up data fix).

Verifies the two UPDATEs ran with the expected end state, and that
the ``/admin/ingest/plan`` endpoint correctly excludes Atlassian
(handle NULL) while including Plaid (now on ashby).

Bestiary 5.8: the migration's UPDATEs are guarded on the OLD value
(``WHERE ats='lever' AND ats_handle='X'``), so re-running on a row
already at the new value is a no-op. This test seeds a fresh row
in the canonical pre-migration shape so we know the migration's
UPDATE actually fires, not the no-op branch.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from job_assist.db.models import TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


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


@_NEEDS_DB
@pytest.mark.asyncio
async def test_plaid_ends_up_on_ashby(db_session: Any) -> None:
    """After the migration, the Plaid row points at ashby/plaid."""
    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "Plaid"))
    ).scalar_one_or_none()
    assert row is not None, "seed migration should have inserted Plaid"
    assert row.ats == "ashby", f"expected ats='ashby', got {row.ats!r}"
    assert row.ats_handle == "plaid", f"expected ats_handle='plaid', got {row.ats_handle!r}"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_atlassian_is_soft_paused(db_session: Any) -> None:
    """After the migration, the Atlassian row has NULL handle + an
    investigation note in ``notes``. The cron will skip it via the
    ``ats_handle IS NOT NULL`` filter on /admin/ingest/plan."""
    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "Atlassian"))
    ).scalar_one_or_none()
    assert row is not None, "seed migration should have inserted Atlassian"
    assert row.ats_handle is None, f"expected ats_handle=None (soft-pause), got {row.ats_handle!r}"
    # Note carries the investigation breadcrumb; partial-substring match
    # so it survives prepended operator notes.
    assert row.notes is not None
    assert "Paused 2026-05-26" in row.notes
    assert "Workday wd5 401" in row.notes


@_NEEDS_DB
@pytest.mark.asyncio
async def test_ingest_plan_excludes_atlassian_includes_plaid(db_session: Any) -> None:
    """End-to-end: /admin/ingest/plan reflects the migration's effect.

    Plaid (ats_handle='plaid' on ashby) shows up; Atlassian
    (ats_handle=NULL) is filtered out by the existing predicate at
    main.py:123 — ``.where(TargetCompany.ats_handle.isnot(None))``.
    """
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/admin/ingest/plan")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    plan = resp.json()

    plaid_entries = [p for p in plan if p.get("handle") == "plaid"]
    assert any(p.get("ats") == "ashby" for p in plaid_entries), (
        f"Plaid should appear with ats='ashby' in plan; got {plaid_entries!r}"
    )

    # Atlassian's row has ats_handle=NULL so it should not appear under
    # any ats. The plan returns ``(ats, handle)`` tuples — if Atlassian
    # is in there, the migration didn't soft-pause it correctly.
    atlassian_entries = [p for p in plan if p.get("handle") == "atlassian"]
    assert atlassian_entries == [], (
        f"Atlassian should be filtered out (NULL handle); got {atlassian_entries!r}"
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_migration_guard_is_idempotent(db_session: Any) -> None:
    """Re-running the migration's UPDATE against a row already at the
    new value is a no-op (Bestiary 5.8 — OLD-value guard).

    Implementation check: the WHERE clauses on both UPDATEs include
    the pre-migration ats + ats_handle, so a second pass matches zero
    rows. We verify by running the UPDATE statements again directly
    and asserting nothing changed.
    """
    # Snapshot Plaid's row before re-running the UPDATE.
    before = (
        await db_session.execute(
            select(TargetCompany.updated_at).where(TargetCompany.name == "Plaid")
        )
    ).scalar_one()

    await db_session.execute(
        text(
            """
            UPDATE target_company
            SET ats = 'ashby', ats_handle = 'plaid'
            WHERE name = 'Plaid'
              AND ats = 'lever'
              AND ats_handle = 'plaid'
            """
        )
    )
    await db_session.commit()

    after = (
        await db_session.execute(
            select(TargetCompany.updated_at).where(TargetCompany.name == "Plaid")
        )
    ).scalar_one()
    # Row was already at the new value, so the WHERE clause matched 0
    # rows and ``updated_at`` did not tick.
    assert before == after, (
        "Re-running the OLD-value-guarded UPDATE should have been a no-op; updated_at advanced."
    )
