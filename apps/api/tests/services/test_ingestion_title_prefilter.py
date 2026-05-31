"""Integration tests for the opt-in title pre-filter
(feat/ingest-title-prefilter).

Three contracts pinned:

  1. **Additive guarantee** — ``apply_title_prefilter=False`` (the
     default) is byte-identical to pre-PR ingestion. The curated-30
     cron, which deliberately keeps non-PM roles for the Companies /
     Stats surfaces, is unaffected. ``peek_title()`` isn't even called
     when the flag is off.
  2. **Filter works when opted in** — ``apply_title_prefilter=True``
     drops non-PM titles **before** ``normalize()`` runs, so they
     never reach the DB. PM-cluster titles still upsert as normal.
  3. **No leaked state on the IngestRun** — the skip counter doesn't
     pollute ``postings_new`` or ``postings_updated``. ``postings_fetched``
     reflects what the adapter returned (pre-filter); the filtered
     count is logged but does NOT add to the DB-side counters.

All DB-gated. Run on CI's postgres service.
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
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _adapter_with_jobs(jobs: list[dict[str, Any]]) -> GreenhouseAdapter:
    """Mock-httpx GreenhouseAdapter that returns the given jobs list."""
    payload = {"jobs": jobs}
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return GreenhouseAdapter(client=mock_client)


def _fake_job(title: str, idx: int) -> dict[str, Any]:
    """Build a Greenhouse-shaped job payload with a controlled title.

    Borrows non-title fields from a real fixture entry so the
    normalize/upsert path doesn't trip on missing keys downstream.
    """
    template: dict[str, Any] = json.loads(json.dumps(_FIXTURE["jobs"][0]))
    template["id"] = 9_000_000 + idx
    template["title"] = title
    # Salt the content so the dedup hash differs per job — the suite
    # needs every fake_job to be a distinct row, not collapsed by
    # content_hash equality.
    template["content"] = f"{template.get('content', '')}\n<!-- prefilter test {idx} -->"
    return template


@_NEEDS_DB
async def test_prefilter_off_is_no_op(db_session: Any) -> None:
    """Contract (1): with ``apply_title_prefilter=False`` (default),
    EVERY adapter row reaches the DB regardless of title. The curated-30
    path must keep ingesting non-PM rows."""
    jobs = [
        _fake_job("Senior Software Engineer", 1),
        _fake_job("Director of Sales", 2),
        _fake_job("Senior Product Manager", 3),
    ]
    run = await IngestionService().ingest_source(
        _adapter_with_jobs(jobs),
        "stripe",
        db_session,
    )
    assert run.status == "success"
    assert run.postings_fetched == 3
    assert run.postings_new == 3, (
        "With the prefilter off, all three rows should land in the DB — "
        "this is the additive-guarantee contract."
    )

    # DB-side row count corroborates the counter.
    rows: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert rows == 3


@_NEEDS_DB
async def test_prefilter_on_drops_non_pm_keeps_pm(db_session: Any) -> None:
    """Contract (2): with ``apply_title_prefilter=True``, only the PM
    row makes it into the DB. The two non-PM rows are dropped before
    normalize/upsert ever runs."""
    jobs = [
        _fake_job("Senior Software Engineer", 10),
        _fake_job("Director of Sales", 11),
        _fake_job("Senior Product Manager", 12),
        _fake_job("Product Designer", 13),  # explicit exclusion — also dropped
        _fake_job("Product Owner", 14),
    ]
    run = await IngestionService().ingest_source(
        _adapter_with_jobs(jobs),
        "stripe",
        db_session,
        apply_title_prefilter=True,
    )
    assert run.status == "success"
    # postings_fetched reflects adapter output (5) — pre-filter happens
    # AFTER fetch, so this counter is unchanged from the off case.
    assert run.postings_fetched == 5
    # Only the two real PM rows landed.
    assert run.postings_new == 2, (
        "Prefilter ON should keep only Senior Product Manager + Product Owner "
        "(2 rows). Software Engineer / Director of Sales / Product Designer "
        f"should drop. Got new={run.postings_new}."
    )
    assert run.postings_updated == 0

    titles_in_db = sorted(
        (
            await db_session.execute(
                select(JobPosting.normalized_title).order_by(JobPosting.normalized_title)
            )
        )
        .scalars()
        .all()
    )
    assert titles_in_db == ["product owner", "senior product manager"], (
        f"DB should contain only the two PM-cluster titles; got {titles_in_db}"
    )


@_NEEDS_DB
async def test_prefilter_skip_counter_does_not_leak_into_db_counters(
    db_session: Any,
) -> None:
    """Contract (3): the title-skip count is logged but NEVER added to
    ``postings_new`` / ``postings_updated`` on the IngestRun. A
    regression here would make the IngestRun row lie about DB state,
    which the Bestiary-5.X correctness tests already catch on a
    different axis."""
    jobs = [
        _fake_job("Software Engineer", 20),  # drop
        _fake_job("Sales Manager", 21),  # drop
        _fake_job("Product Engineer", 22),  # drop (adjacent-distinct)
    ]
    run = await IngestionService().ingest_source(
        _adapter_with_jobs(jobs),
        "stripe",
        db_session,
        apply_title_prefilter=True,
    )
    assert run.status == "success"
    assert run.postings_fetched == 3
    assert run.postings_new == 0, (
        "Zero rows should land in the DB when every adapter row is filtered."
    )
    assert run.postings_updated == 0

    rows: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert rows == 0, "DB must be empty — every row was filtered before upsert."
