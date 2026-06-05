"""Tests for ``adapter_config`` passing through the seed loader
(feat/dm-employer-ingestion).

``adapter_config`` was missing from ``_SEED_FIELDS``, so it was silently
dropped by ``_project_row`` — Workday tenants (which require
``{wd_number, site}``) and iCIMS non-default URLs could never be seeded,
which is why curated Workday rows sat at null handles and never crawled.

The pure ``_project_row`` test runs anywhere (no DB). The persistence test
is DB-gated like the rest of the seed suite.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from job_assist.db.models import TargetCompany
from job_assist.seed import _project_row, seed_from_rows

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def test_project_row_retains_adapter_config() -> None:
    """Regression: the field must survive projection, not be dropped."""
    cfg = {"wd_number": "wd5", "site": "athene_careers"}
    row = _project_row(
        {
            "name": "Athene",
            "tier": 2,
            "ats": "workday",
            "ats_handle": "athene",
            "adapter_config": cfg,
        }
    )
    assert row["adapter_config"] == cfg
    assert row["ats_handle"] == "athene"


def test_project_row_still_drops_unknown_keys() -> None:
    """The allowlist still rejects keys outside _SEED_FIELDS (e.g. id)."""
    row = _project_row(
        {"name": "X", "tier": 1, "id": "tamper", "created_at": "nope", "adapter_config": {"a": 1}}
    )
    assert "id" not in row
    assert "created_at" not in row
    assert row["adapter_config"] == {"a": 1}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_seed_persists_adapter_config_on_insert(db_session: Any) -> None:
    name = f"WorkdayCo-{uuid.uuid4().hex[:6]}"
    cfg = {"wd_number": "wd5", "site": "voya_jobs"}

    inserted, skipped, _ = await seed_from_rows(
        db_session,
        [
            {
                "name": name,
                "tier": 2,
                "ats": "workday",
                "ats_handle": "godirect",
                "adapter_config": cfg,
            }
        ],
    )
    assert (inserted, skipped) == (1, 0)

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == name))
    ).scalar_one()
    assert row.adapter_config == cfg
    assert row.ats_handle == "godirect"
