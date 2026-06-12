"""Tests for the ``backfill_nullables`` mode on seed_from_rows
(feat/outcome-company-linking).

The PR widens the seed semantics so the operator can re-POST the seed
JSON with ``domain`` (and other nullable fields) hand-filled to patch
existing rows. The contract:

  * Existing row, seed has value, DB has NULL  → DB filled.
  * Existing row, seed has value, DB has existing value → DB UNTOUCHED.
  * New row → inserted as before, independent of the flag.

These DB-gated tests pin all three behaviours.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from job_assist.db.models import TargetCompany
from job_assist.seed import seed_from_rows

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _row(name: str, **extras: Any) -> dict[str, Any]:
    return {"name": name, "tier": 1, **extras}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_backfill_fills_null_domain_on_existing_row(db_session: Any) -> None:
    name = f"TestCo-{uuid.uuid4().hex[:6]}"
    db_session.add(TargetCompany(name=name, tier=1, ats="greenhouse", domain=None))
    await db_session.commit()

    inserted, skipped, backfilled = await seed_from_rows(
        db_session,
        [_row(name, domain="testco.com")],
        backfill_nullables=True,
    )
    assert (inserted, skipped, backfilled) == (0, 1, 1)

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == name))
    ).scalar_one()
    assert row.domain == "testco.com"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_existing_domain(db_session: Any) -> None:
    name = f"TestCo-{uuid.uuid4().hex[:6]}"
    db_session.add(TargetCompany(name=name, tier=1, ats="greenhouse", domain="existing.com"))
    await db_session.commit()

    inserted, skipped, backfilled = await seed_from_rows(
        db_session,
        [_row(name, domain="should-not-apply.com")],
        backfill_nullables=True,
    )
    assert (inserted, skipped, backfilled) == (0, 1, 0)

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == name))
    ).scalar_one()
    assert row.domain == "existing.com"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_backfill_fills_null_tier_on_existing_row(db_session: Any) -> None:
    """fix(audit): ``tier`` was excluded from the backfill loop on the stale
    premise that it's NOT NULL — it has been nullable since the broad-
    ingestion expansion, so a NULL-tier row (e.g. a broad shell being
    promoted via the seed) silently never got the seed's tier and stayed
    outside the daily plan's ``tier IS NOT NULL`` gate while the response
    claimed it was backfilled."""
    name = f"TestCo-{uuid.uuid4().hex[:6]}"
    db_session.add(TargetCompany(name=name, tier=None, ats="greenhouse", domain="testco.com"))
    await db_session.commit()

    inserted, skipped, backfilled = await seed_from_rows(
        db_session,
        [_row(name, domain="testco.com")],  # _row supplies tier=1
        backfill_nullables=True,
    )
    assert (inserted, skipped, backfilled) == (0, 1, 1)

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == name))
    ).scalar_one()
    assert row.tier == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_existing_tier(db_session: Any) -> None:
    """Fill-if-NULL only: a hand-set tier survives a re-seed with a different one."""
    name = f"TestCo-{uuid.uuid4().hex[:6]}"
    db_session.add(TargetCompany(name=name, tier=3, ats="greenhouse", domain=None))
    await db_session.commit()

    await seed_from_rows(
        db_session,
        [_row(name)],  # seed says tier=1; DB row already has 3
        backfill_nullables=True,
    )
    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == name))
    ).scalar_one()
    assert row.tier == 3


@_NEEDS_DB
@pytest.mark.asyncio
async def test_backfill_off_leaves_null_domain_alone(db_session: Any) -> None:
    """Default behaviour (flag off) — existing rows untouched, no-op."""
    name = f"TestCo-{uuid.uuid4().hex[:6]}"
    db_session.add(TargetCompany(name=name, tier=1, ats="greenhouse", domain=None))
    await db_session.commit()

    inserted, skipped, backfilled = await seed_from_rows(
        db_session,
        [_row(name, domain="testco.com")],
        backfill_nullables=False,
    )
    assert (inserted, skipped, backfilled) == (0, 1, 0)

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == name))
    ).scalar_one()
    assert row.domain is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_backfill_still_inserts_new_rows(db_session: Any) -> None:
    """Backfill mode must not block new-row inserts — both behaviours
    coexist on one POST."""
    existing_name = f"TestCo-{uuid.uuid4().hex[:6]}"
    new_name = f"TestCo-{uuid.uuid4().hex[:6]}"
    db_session.add(TargetCompany(name=existing_name, tier=1, ats="greenhouse", domain=None))
    await db_session.commit()

    inserted, skipped, backfilled = await seed_from_rows(
        db_session,
        [
            _row(existing_name, domain="existing.com"),
            _row(new_name, ats="ashby", domain="new.com"),
        ],
        backfill_nullables=True,
    )
    assert (inserted, skipped, backfilled) == (1, 1, 1)


# ── feat/warm-path-ingest: seed rows may carry a cohort source ───────────────


def test_project_row_accepts_warm_path_source() -> None:
    from job_assist.seed import _project_row

    row = _project_row(
        {
            "name": "John Deere",
            "tier": None,
            "ats": "workday",
            "domain": "deere.com",
            "source": "warm_path",
        }
    )
    assert row["source"] == "warm_path"


def test_project_row_rejects_unknown_source() -> None:
    import pytest as _pytest

    from job_assist.seed import _project_row

    with _pytest.raises(ValueError, match="source must be one of"):
        _project_row({"name": "X", "tier": None, "source": "bogus"})


# ── fix(audit): ats/tier vocabulary validation (422 naming the bad field) ────


def test_project_row_rejects_unknown_ats() -> None:
    import pytest as _pytest

    from job_assist.seed import _project_row

    # Pre-fix a bad ats string sailed through and 500'd at the SAEnum cast.
    with _pytest.raises(ValueError, match="ats must be one of"):
        _project_row({"name": "X", "tier": 1, "ats": "taleo"})


def test_project_row_rejects_out_of_range_tier() -> None:
    import pytest as _pytest

    from job_assist.seed import _project_row

    # Pre-fix an out-of-range tier inserted silently.
    with _pytest.raises(ValueError, match="tier must be null or an integer 1-4"):
        _project_row({"name": "X", "tier": 9})
    with _pytest.raises(ValueError, match="tier must be null or an integer 1-4"):
        _project_row({"name": "X", "tier": "high"})


def test_project_row_accepts_valid_tier_and_null_tier() -> None:
    from job_assist.seed import _project_row

    assert _project_row({"name": "X", "tier": 4})["tier"] == 4
    assert _project_row({"name": "X", "tier": None})["tier"] is None
