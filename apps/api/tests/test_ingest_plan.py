"""Tests for the GET /admin/ingest/plan endpoint that drives the daily cron.

Verifies the four filter rules + ordering. DB-gated because the endpoint
queries real tables; the asgi test client talks to the FastAPI app
end-to-end against the postgres service.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.enums import ClosedChannelReason
from job_assist.db.models.closed_channel import ClosedChannel
from job_assist.db.models.target_company import TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _tc(
    name: str,
    *,
    ats: str = "greenhouse",
    handle: str | None = "handle-x",
    tier: int | None = 3,
) -> TargetCompany:
    return TargetCompany(name=name, ats=ats, ats_handle=handle, tier=tier)


async def _call_plan(db_session: Any) -> list[dict[str, str]]:
    """Hit GET /admin/ingest/plan with the db_session injected as the dependency."""
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/admin/ingest/plan")
        assert resp.status_code == 200, resp.text
        plan: list[dict[str, str]] = resp.json()
        return plan
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── Tests ─────────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_returns_only_supported_ats(db_session: Any) -> None:
    """Rows with ats='unknown' must NOT appear in the plan.

    Workday is a supported adapter as of PR #33, so workday rows with a
    handle now show up in the plan. adapter_config validation happens at
    trigger time, not plan time — the plan endpoint just lists what's
    eligible for ingest.
    """
    db_session.add_all(
        [
            _tc("AlphaCo", ats="greenhouse", handle="alphaco", tier=1),
            _tc("BetaCo", ats="lever", handle="betaco", tier=2),
            _tc("GammaCo", ats="ashby", handle="gammaco", tier=3),
            _tc("WorkdayCo", ats="workday", handle="workdayco", tier=2),
            _tc("UnknownCo", ats="unknown", handle=None, tier=3),
        ]
    )
    await db_session.commit()

    plan = await _call_plan(db_session)
    handles = {item["handle"] for item in plan}
    assert handles == {"alphaco", "betaco", "gammaco", "workdayco"}


@_NEEDS_DB
async def test_skips_unknown_handle(db_session: Any) -> None:
    """A row with ats='greenhouse' but ats_handle=NULL must be excluded."""
    db_session.add_all(
        [
            _tc("HasHandle", ats="greenhouse", handle="hashandle", tier=1),
            _tc("NoHandle", ats="greenhouse", handle=None, tier=1),
        ]
    )
    await db_session.commit()

    plan = await _call_plan(db_session)
    handles = {item["handle"] for item in plan}
    assert "hashandle" in handles
    assert handles == {"hashandle"}


@_NEEDS_DB
async def test_skips_closed_channels(db_session: Any) -> None:
    """A target_company with an ACTIVE (unsealed_at IS NULL) closed_channel must be excluded."""
    active = _tc("SealedCo", ats="greenhouse", handle="sealedco", tier=2)
    unsealed = _tc("UnsealedCo", ats="greenhouse", handle="unsealedco", tier=2)
    open_co = _tc("OpenCo", ats="greenhouse", handle="openco", tier=2)
    db_session.add_all([active, unsealed, open_co])
    await db_session.flush()

    db_session.add(
        ClosedChannel(
            target_company_id=active.id,
            company_name=active.name,
            reason=ClosedChannelReason.multiple_rejections,
            rejection_count=3,
            closed_at=datetime.now(tz=UTC),
            unsealed_at=None,  # active seal
        )
    )
    db_session.add(
        ClosedChannel(
            target_company_id=unsealed.id,
            company_name=unsealed.name,
            reason=ClosedChannelReason.other,
            rejection_count=1,
            closed_at=datetime.now(tz=UTC),
            unsealed_at=datetime.now(tz=UTC),  # already reopened
        )
    )
    await db_session.commit()

    plan = await _call_plan(db_session)
    handles = {item["handle"] for item in plan}
    assert "sealedco" not in handles, "Active closed_channel row must be filtered"
    assert "unsealedco" in handles, "Unsealed row is no longer closed; must remain"
    assert "openco" in handles


@_NEEDS_DB
async def test_ordered_by_tier_then_name(db_session: Any) -> None:
    """Tier-1 entries come before Tier-2/3/4; within a tier, alphabetical by name."""
    db_session.add_all(
        [
            _tc("Zeta T2", ats="greenhouse", handle="zeta", tier=2),
            _tc("Alpha T3", ats="greenhouse", handle="alpha", tier=3),
            _tc("Mid T1", ats="greenhouse", handle="mid", tier=1),
            _tc("Bravo T1", ats="greenhouse", handle="bravo", tier=1),
            _tc("Zulu T4", ats="lever", handle="zulu", tier=4),
        ]
    )
    await db_session.commit()

    plan = await _call_plan(db_session)
    handles_in_order = [item["handle"] for item in plan]
    assert handles_in_order == ["bravo", "mid", "zeta", "alpha", "zulu"]


@_NEEDS_DB
async def test_response_shape_is_list_of_dicts(db_session: Any) -> None:
    """The shape the cron's python script expects: [{"ats": str, "handle": str}, ...]."""
    db_session.add(_tc("OnlyOne", ats="ashby", handle="onlyone", tier=1))
    await db_session.commit()

    plan = await _call_plan(db_session)
    assert isinstance(plan, list)
    assert len(plan) == 1
    entry = plan[0]
    assert set(entry.keys()) == {"ats", "handle"}
    assert entry["ats"] == "ashby"
    assert entry["handle"] == "onlyone"


@_NEEDS_DB
async def test_excludes_broad_ingest_shells_tier_null(db_session: Any) -> None:
    """Curated/broad separation (Slice 2): a target_company shell with
    ``tier=NULL`` (created by the broad-ingest runner) must NOT appear in
    the curated daily-cron plan — otherwise it would be ingested WITHOUT
    the title pre-filter, flooding the DB with the non-PM long tail.
    Curated companies (tier 1-4) still appear."""
    db_session.add_all(
        [
            _tc("CuratedCo", ats="greenhouse", handle="curatedco", tier=1),
            # Broad-ingest shell — handle set, ingestable ATS, but tier NULL.
            _tc("BroadShellCo", ats="greenhouse", handle="broadshellco", tier=None),
        ]
    )
    await db_session.commit()

    plan = await _call_plan(db_session)
    handles = {item["handle"] for item in plan}
    assert "curatedco" in handles, "curated company (tier set) must be in the plan"
    assert "broadshellco" not in handles, (
        "broad-ingest shell (tier NULL) must be excluded from the unfiltered curated daily plan"
    )


@_NEEDS_DB
async def test_empty_db_returns_empty_list(db_session: Any) -> None:
    plan = await _call_plan(db_session)
    assert plan == []


@_NEEDS_DB
async def test_excludes_non_curated_sources_with_leftover_tier_handle(db_session: Any) -> None:
    """fix/plan-source-filter: a row reactivated into another cohort can keep
    its old tier+handle — Athene (source='warm_path', tier=2, handle set)
    leaked into the daily plan as a guaranteed-zero free-adapter fetch. The
    plan now filters POSITIVELY on source='curated', closing the same hole
    for 'deactivated' rows too."""
    curated = _tc("CuratedCo2", ats="greenhouse", handle="curatedco2", tier=1)
    warm = _tc("Athene", ats="workday", handle="athene", tier=2)
    warm.source = "warm_path"
    deactivated = _tc("PausedCo", ats="greenhouse", handle="pausedco", tier=2)
    deactivated.source = "deactivated"
    db_session.add_all([curated, warm, deactivated])
    await db_session.commit()

    plan = await _call_plan(db_session)
    handles = {item["handle"] for item in plan}
    assert "curatedco2" in handles
    assert "athene" not in handles, "warm_path rows must never ride the daily plan"
    assert "pausedco" not in handles, "deactivated rows must never ride the daily plan"
