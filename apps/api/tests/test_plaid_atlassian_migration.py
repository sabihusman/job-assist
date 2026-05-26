"""Tests for migration ``c3d4e5f6a7b8`` — Plaid + Atlassian ATS reconfig
(PR #63 follow-up data fix).

Why these tests seed rows themselves
─────────────────────────────────────
The original seed migration ``ed7dbe91ab45_seed_target_companies`` inserted
``Plaid`` and ``Atlassian`` rows, but the very next day's migration
``a1f3c0b8e5d2_remove_target_company_seed_data`` deleted them. So a fresh
CI test database has NO Plaid/Atlassian rows — when the data-fix migration
``c3d4e5f6a7b8`` runs during ``alembic upgrade head`` at test-DB setup,
its ``WHERE name='Plaid' AND ats='lever' AND ats_handle='plaid'`` clause
matches zero rows and the UPDATE is a vacuous no-op.

Production has the rows because the operator re-seeded them manually
after the removal. To test the migration's SQL contract meaningfully,
each test below seeds the pre-migration row shape (``lever/plaid``,
``lever/atlassian``) and then re-executes the migration's UPDATE
statements directly. The UPDATE strings here are the exact same ones
in the migration file — if either drifts, both should be updated
together. See Bestiary 5.8 for the OLD-value-guard pattern.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from job_assist.db.models import TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── UPDATE statements — mirrors the migration's body ────────────────────────


_PLAID_UPDATE = text(
    """
    UPDATE target_company
    SET ats = 'ashby',
        ats_handle = 'plaid'
    WHERE name = 'Plaid'
      AND ats = 'lever'
      AND ats_handle = 'plaid'
    """
)


_ATLASSIAN_UPDATE = text(
    r"""
    UPDATE target_company
    SET ats_handle = NULL,
        notes = COALESCE(notes, '') ||
            E'\nATS unknown - lever/atlassian returned 404, ' ||
            E'Workday wd5 401 on all site names. ' ||
            E'Investigate in browser DevTools and update. ' ||
            E'Paused 2026-05-26.'
    WHERE name = 'Atlassian'
      AND ats = 'lever'
      AND ats_handle = 'atlassian'
    """
)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _seed_pre_migration_rows(db_session: Any) -> None:
    """Insert Plaid + Atlassian in their pre-migration shape (``lever``)."""
    db_session.add(
        TargetCompany(
            name="Plaid",
            ats="lever",
            ats_handle="plaid",
            tier=3,
        )
    )
    db_session.add(
        TargetCompany(
            name="Atlassian",
            ats="lever",
            ats_handle="atlassian",
            tier=3,
        )
    )
    await db_session.commit()


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


# ── Tests ────────────────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_plaid_update_writes_ashby_handle(db_session: Any) -> None:
    """After the migration's UPDATE fires on a pre-migration Plaid row,
    the row is at ``ats='ashby', ats_handle='plaid'``."""
    await _seed_pre_migration_rows(db_session)
    await db_session.execute(_PLAID_UPDATE)
    await db_session.commit()

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "Plaid"))
    ).scalar_one()
    assert row.ats == "ashby", f"expected ats='ashby', got {row.ats!r}"
    assert row.ats_handle == "plaid", f"expected ats_handle='plaid', got {row.ats_handle!r}"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_atlassian_update_clears_handle_and_writes_note(db_session: Any) -> None:
    """After the migration's UPDATE fires on a pre-migration Atlassian
    row, ``ats_handle`` is NULL and ``notes`` carries the investigation
    breadcrumb. The cron skips this row via the existing
    ``ats_handle IS NOT NULL`` predicate at main.py:123."""
    await _seed_pre_migration_rows(db_session)
    await db_session.execute(_ATLASSIAN_UPDATE)
    await db_session.commit()

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "Atlassian"))
    ).scalar_one()
    assert row.ats_handle is None, f"expected ats_handle=None (soft-pause), got {row.ats_handle!r}"
    assert row.notes is not None
    assert "Paused 2026-05-26" in row.notes
    assert "Workday wd5 401" in row.notes


@_NEEDS_DB
@pytest.mark.asyncio
async def test_ingest_plan_excludes_atlassian_includes_plaid(db_session: Any) -> None:
    """End-to-end: after both UPDATEs, ``/admin/ingest/plan`` lists
    Plaid (now on ashby) and silently filters out Atlassian (NULL handle).

    Filter lives at main.py:123:
        .where(TargetCompany.ats_handle.isnot(None))
    """
    await _seed_pre_migration_rows(db_session)
    await db_session.execute(_PLAID_UPDATE)
    await db_session.execute(_ATLASSIAN_UPDATE)
    await db_session.commit()

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

    atlassian_entries = [p for p in plan if p.get("handle") == "atlassian"]
    assert atlassian_entries == [], (
        f"Atlassian should be filtered out (NULL handle); got {atlassian_entries!r}"
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_old_value_guard_is_no_op_when_already_at_new_value(db_session: Any) -> None:
    """Bestiary 5.8: re-running the UPDATE against a row already at the
    new value matches zero rows. Seed Plaid directly at ``ashby/plaid``
    and verify the migration's UPDATE doesn't tick ``updated_at``."""
    # Seed Plaid directly at the post-migration value.
    db_session.add(
        TargetCompany(
            name="Plaid",
            ats="ashby",
            ats_handle="plaid",
            tier=3,
        )
    )
    await db_session.commit()

    before = (
        await db_session.execute(
            select(TargetCompany.updated_at).where(TargetCompany.name == "Plaid")
        )
    ).scalar_one()

    # Re-execute the migration's UPDATE — the OLD-value guard
    # (``ats='lever'``) means zero rows match.
    await db_session.execute(_PLAID_UPDATE)
    await db_session.commit()

    after = (
        await db_session.execute(
            select(TargetCompany.updated_at).where(TargetCompany.name == "Plaid")
        )
    ).scalar_one()
    assert before == after, (
        "OLD-value-guarded UPDATE should have matched zero rows; updated_at advanced."
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_update_does_not_touch_unrelated_rows(db_session: Any) -> None:
    """The WHERE clauses are tight — a different company with the same
    ats_handle by coincidence must not be updated."""
    await _seed_pre_migration_rows(db_session)
    # A non-Plaid company that happens to share the handle string.
    db_session.add(
        TargetCompany(
            name=f"NotPlaid-{uuid.uuid4().hex[:6]}",
            ats="lever",
            ats_handle="plaid",  # same handle as Plaid (unlikely but possible)
            tier=3,
        )
    )
    await db_session.commit()

    await db_session.execute(_PLAID_UPDATE)
    await db_session.commit()

    not_plaid = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name.like("NotPlaid-%")))
    ).scalar_one()
    assert not_plaid.ats == "lever", (
        f"Sibling row sharing the handle must not be updated; got ats={not_plaid.ats!r}"
    )
    assert not_plaid.ats_handle == "plaid"
