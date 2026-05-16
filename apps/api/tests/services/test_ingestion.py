"""Integration tests for IngestionService counter accuracy.

Production observation that motivated these tests
─────────────────────────────────────────────────
A first run against Stripe inserted 487 job_posting rows but the
IngestRun reported postings_new=0, postings_updated=487. The counters
were inverted relative to the actual DB writes.

These tests pin the contract:
  * Empty table  → every posting counts as new, none as updated.
  * Re-ingest    → every posting counts as updated, none as new.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select

from job_assist.adapters.greenhouse import GreenhouseAdapter
from job_assist.db.models.job_posting import JobPosting
from job_assist.services.ingestion import IngestionService

_FIXTURE_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "greenhouse_stripe.json"
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text())

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _make_adapter(jobs: list[dict[str, Any]] | None = None) -> GreenhouseAdapter:
    """Adapter wired to a mock httpx client serving the local fixture."""
    payload = {"jobs": jobs if jobs is not None else _FIXTURE["jobs"]}
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return GreenhouseAdapter(client=mock_client)


@_NEEDS_DB
async def test_counters_new_vs_updated(db_session: Any) -> None:
    """First run → all new; second run with identical data → all updated."""
    expected = len(_FIXTURE["jobs"])
    service = IngestionService()

    # Sanity: table starts empty (the db_session fixture truncates between tests).
    starting_count: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert starting_count == 0, "Test precondition: job_posting must be empty"

    # ── First run: every posting is new ──────────────────────────────────────
    run1 = await service.ingest_source(_make_adapter(), "stripe", db_session)
    assert run1.status == "success"
    assert run1.postings_fetched == expected
    assert run1.postings_new == expected, (
        f"First run should count {expected} new postings; got {run1.postings_new}"
    )
    assert run1.postings_updated == 0, (
        f"First run should count 0 updated postings; got {run1.postings_updated}"
    )

    rows_after_first: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert rows_after_first == expected, (
        "DB should contain exactly one row per fixture job after the first run"
    )

    # ── Second run with identical data: every posting is an update ──────────
    run2 = await service.ingest_source(_make_adapter(), "stripe", db_session)
    assert run2.status == "success"
    assert run2.postings_fetched == expected
    assert run2.postings_new == 0, (
        f"Second run should count 0 new postings; got {run2.postings_new}"
    )
    assert run2.postings_updated == expected, (
        f"Second run should count {expected} updated postings; got {run2.postings_updated}"
    )

    rows_after_second: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert rows_after_second == expected, (
        "Idempotency: row count must not change on re-ingest of identical data"
    )
