"""DB-gated tests for POST /admin/companies/crawl-config (coverage expansion).

The seed endpoint only inserts / backfills NULLs, so it can't *change* an
existing tier/source. This endpoint is the lever for:
  * DEACTIVATING an off-profile company (tier=null + source='deactivated') so it
    drops out of BOTH the curated daily-cron plan (tier IS NOT NULL) and the
    Apify Workday/iCIMS sweep (source == 'curated') — without deleting the row;
  * PROMOTING a broad shell (tier=null) into the curated cron (tier=1-4).

DB-gated (need TEST_DATABASE_URL); run on CI's postgres service.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models.target_company import TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _company(name: str, *, tier: int | None, source: str, ats: str = "workday") -> TargetCompany:
    return TargetCompany(
        name=name,
        ats=ats,  # type: ignore[arg-type]
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
        tier=tier,
        source=source,
        domain=f"{name.lower().replace(' ', '')}.com",
    )


async def _post(client: AsyncClient, rows: list[dict[str, Any]]) -> Any:
    return await client.post("/admin/companies/crawl-config", json=rows)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_deactivate_drops_from_plan_and_keeps_row(db_session: Any) -> None:
    """tier=null + source='deactivated' removes the company from the ingest plan
    but preserves the row + domain (Gmail-match history)."""
    from job_assist.main import app

    carrier = _company("Athene-Test", tier=2, source="curated")
    db_session.add(carrier)
    await db_session.commit()
    original_domain = carrier.domain

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(client, [{"name": "Athene-Test", "tier": None, "source": "deactivated"}])
        assert resp.status_code == 200, resp.text
        assert resp.json()["counts"]["updated"] == 1

        plan = (await client.get("/admin/ingest/plan")).json()

    handles = {p["handle"] for p in plan}
    assert carrier.ats_handle not in handles  # dropped from the curated plan

    # Row preserved (NOT deleted) with domain intact.
    await db_session.refresh(carrier)
    assert carrier.tier is None
    assert carrier.source == "deactivated"
    assert carrier.domain == original_domain


@_NEEDS_DB
@pytest.mark.asyncio
async def test_promote_broad_shell_into_plan(db_session: Any) -> None:
    """Setting tier on a tier=null broad shell adds it to the curated plan."""
    from job_assist.main import app

    shell = _company("Altruist-Test", tier=None, source="broad", ats="ashby")
    db_session.add(shell)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # Absent 'source' key => source untouched (stays 'broad').
        resp = await _post(client, [{"name": "Altruist-Test", "tier": 2}])
        assert resp.status_code == 200, resp.text
        plan = (await client.get("/admin/ingest/plan")).json()

    handles = {p["handle"] for p in plan}
    assert shell.ats_handle in handles
    await db_session.refresh(shell)
    assert shell.tier == 2
    assert shell.source == "broad"  # untouched (key absent)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_unknown_name_reported_not_500(db_session: Any) -> None:
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(client, [{"name": "Nonexistent-Co", "tier": None}])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["not_found"] == ["Nonexistent-Co"]
    assert body["counts"]["updated"] == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_invalid_tier_rejected_no_partial_write(db_session: Any) -> None:
    """A bad value rejects the whole batch (400) with no partial commit."""
    from job_assist.main import app

    co = _company("Valid-Co", tier=1, source="curated")
    db_session.add(co)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(
            client,
            [
                {"name": "Valid-Co", "tier": 3},  # would-be-valid
                {"name": "Valid-Co", "tier": 5},  # invalid -> rejects batch
            ],
        )
    assert resp.status_code == 400

    # The first (valid) patch must NOT have committed — validation is up front.
    await db_session.refresh(co)
    assert co.tier == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_invalid_source_rejected(db_session: Any) -> None:
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(client, [{"name": "Whatever", "source": "garbage"}])
    assert resp.status_code == 400


@_NEEDS_DB
@pytest.mark.asyncio
async def test_route_unknown_ats_onto_apify_workday_path(db_session: Any) -> None:
    """Flipping ats='unknown' -> 'workday' is the ONLY way to route an
    IP-blocked employer onto the Apify sweep — discover-ats can't detect those
    boards. This is the mechanism #4 needs for Goldman/Amex/etc."""
    from job_assist.main import app

    firm = _company("Goldman-Test", tier=2, source="curated", ats="unknown")
    db_session.add(firm)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(client, [{"name": "Goldman-Test", "ats": "workday"}])
        assert resp.status_code == 200, resp.text
        assert resp.json()["counts"]["updated"] == 1

    await db_session.refresh(firm)
    ats_value = firm.ats.value if hasattr(firm.ats, "value") else firm.ats
    assert ats_value == "workday"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_ats_handle_patched_independently(db_session: Any) -> None:
    """ats_handle is mutable too (null => set NULL; string => set), so a free
    greenhouse/lever board discovered out-of-band can be wired up by hand."""
    from job_assist.main import app

    co = _company("Lever-Test", tier=1, source="curated", ats="unknown")
    db_session.add(co)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(
            client, [{"name": "Lever-Test", "ats": "lever", "ats_handle": "levertest"}]
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["counts"]["updated"] == 1

    await db_session.refresh(co)
    assert co.ats_handle == "levertest"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_no_op_ats_patch_reported_unchanged(db_session: Any) -> None:
    """Re-applying the SAME ats must report 'unchanged', not churn a write —
    tc.ats is an Enum so it must be compared against its .value, not the raw
    string (AtsKind.workday != 'workday')."""
    from job_assist.main import app

    co = _company("Workday-Test", tier=2, source="curated", ats="workday")
    db_session.add(co)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(client, [{"name": "Workday-Test", "ats": "workday"}])
        assert resp.status_code == 200, resp.text
        body = resp.json()

    assert body["unchanged"] == ["Workday-Test"]
    assert body["counts"]["updated"] == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_invalid_ats_rejected_no_partial_write(db_session: Any) -> None:
    """A bad ats rejects the whole batch (400) with no partial commit."""
    from job_assist.main import app

    co = _company("AtsGuard-Test", tier=1, source="curated", ats="unknown")
    db_session.add(co)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await _post(
            client,
            [
                {"name": "AtsGuard-Test", "ats": "workday"},  # would-be-valid
                {"name": "AtsGuard-Test", "ats": "monster"},  # invalid -> rejects batch
            ],
        )
    assert resp.status_code == 400

    await db_session.refresh(co)
    ats_value = co.ats.value if hasattr(co.ats, "value") else co.ats
    assert ats_value == "unknown"  # first (valid) patch did NOT commit
