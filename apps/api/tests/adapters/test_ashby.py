"""Tests for the Ashby adapter.

Unit tests (no DB, no network)
  test_filters_unlisted_and_internal — isListed=false / isInternal=true dropped
  test_descriptionPlain_wins         — plain preferred over HTML
  test_html_fallback_when_no_plain   — strip HTML when descriptionPlain missing
  test_isRemote_overrides_location   — isRemote=true → remote_type=remote
  test_secondary_locations_collected — secondaryLocations contribute
  test_salary_range_parsed           — "$180K - $230K" parses to two ints
  test_salary_single_value           — "$210K" parses to equal min/max
  test_no_compensation_field         — missing comp → all-None salary fields
  test_fetch_returns_filtered_list   — fetch_postings returns 3 of 4 fixture rows

Integration tests (require TEST_DATABASE_URL)
  test_idempotency_counts            — second run = 0 new, N updated
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from job_assist.adapters.ashby import AshbyAdapter
from job_assist.adapters.base import RawPosting

_FIXTURE_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "ashby_synthetic.json"
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text())
_JOBS: list[dict[str, Any]] = _FIXTURE["jobs"]

# Index of the row that should be filtered out (isListed=false, isInternal=true).
_UNLISTED_IDX = 2
_VISIBLE_JOBS = [j for i, j in enumerate(_JOBS) if i != _UNLISTED_IDX]

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_adapter(
    payload: dict[str, Any] | None = None,
    status_code: int = 200,
) -> AshbyAdapter:
    """AshbyAdapter wired to a mock httpx client serving the fixture by default."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = payload if payload is not None else _FIXTURE

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return AshbyAdapter(client=mock_client)


def _raw(index: int) -> RawPosting:
    job = _JOBS[index]
    return RawPosting(source_job_id=str(job["id"]), raw_payload=job)


# ── Normalization ─────────────────────────────────────────────────────────────


class TestNormalize:
    def test_descriptionPlain_wins(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Example")
        assert "platform roadmap" in norm.jd_text
        assert "<" not in norm.jd_text
        assert "HTML fallback" not in norm.jd_text

    def test_html_fallback_when_no_plain(self) -> None:
        """Posting #1 omits descriptionPlain → HTML stripped from descriptionHtml."""
        norm = _make_adapter().normalize(_raw(1), "Example")
        assert "<" not in norm.jd_text
        assert "Lead PM, Growth" in norm.jd_text
        assert "Coach the growth team" in norm.jd_text

    def test_isRemote_overrides_location(self) -> None:
        """isRemote=true → remote_type=remote even though location_raw is set."""
        norm = _make_adapter().normalize(_raw(1), "Example")
        assert norm.remote_type == "remote"

    def test_onsite_when_not_remote(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Example")
        # Both primary (SF) and secondary (NY) are onsite US — derived = onsite.
        assert norm.remote_type == "onsite"

    def test_secondary_locations_collected(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Example")
        # SF + NY → two entries in locations_normalized.
        assert len(norm.locations_normalized) == 2

    def test_salary_range_parsed(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Example")
        assert norm.salary_min == 180_000
        assert norm.salary_max == 230_000
        assert norm.salary_currency == "USD"
        assert norm.salary_period == "annual"

    def test_salary_single_value(self) -> None:
        norm = _make_adapter().normalize(_raw(1), "Example")
        assert norm.salary_min == 210_000
        assert norm.salary_max == 210_000
        assert norm.salary_currency == "USD"
        assert norm.salary_period == "annual"

    def test_no_compensation_field(self) -> None:
        """Posting #3 has no `compensation` key — all salary fields stay None/unknown."""
        norm = _make_adapter().normalize(_raw(3), "Example")
        assert norm.salary_min is None
        assert norm.salary_max is None
        assert norm.salary_currency is None
        assert norm.salary_period == "unknown"

    def test_seniority_role_family(self) -> None:
        adapter = _make_adapter()

        senior = adapter.normalize(_raw(0), "Example")
        assert senior.seniority_level == "senior_pm"
        assert senior.role_family == "product_management"

        lead = adapter.normalize(_raw(1), "Example")
        assert lead.seniority_level == "lead_pm"
        assert lead.role_family == "product_management"

        apm = adapter.normalize(_raw(3), "Example")
        assert apm.seniority_level == "apm"
        assert apm.role_family == "product_management"

    def test_posted_at_parsed(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Example")
        assert norm.posted_at is not None
        assert norm.posted_at.year == 2026
        assert norm.posted_at.month == 5

    def test_source_and_apply_urls(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Example")
        assert norm.source_url.startswith("https://jobs.ashbyhq.com/")
        assert norm.apply_url is not None
        assert norm.apply_url.endswith("/application")


# ── fetch_postings + filtering ────────────────────────────────────────────────


class TestFetchPostings:
    async def test_filters_unlisted_and_internal(self) -> None:
        """The isListed=false / isInternal=true row must not reach the pipeline."""
        adapter = _make_adapter()
        raws = await adapter.fetch_postings("example")
        ids = {r.source_job_id for r in raws}
        assert _JOBS[_UNLISTED_IDX]["id"] not in ids
        assert len(raws) == len(_VISIBLE_JOBS)

    async def test_404_raises_handle_not_found(self) -> None:
        """Bestiary 5.9 — 404 → HandleNotFoundError (distinct status)."""
        from job_assist.adapters.base import HandleNotFoundError

        adapter = _make_adapter(status_code=404)
        with pytest.raises(HandleNotFoundError) as exc_info:
            await adapter.fetch_postings("nonexistent")
        assert exc_info.value.ats == "ashby"
        assert exc_info.value.handle == "nonexistent"

    async def test_handles_non_dict_payload(self) -> None:
        adapter = _make_adapter(payload=["not", "a", "dict"])  # type: ignore[arg-type]
        assert await adapter.fetch_postings("example") == []

    async def test_handles_missing_jobs_key(self) -> None:
        adapter = _make_adapter(payload={"apiVersion": "1"})
        assert await adapter.fetch_postings("example") == []


# ── Integration ───────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_idempotency_counts(db_session: Any) -> None:
    """Re-ingesting identical Ashby data must report all-updates on run 2."""
    from job_assist.services.ingestion import IngestionService

    expected = len(_VISIBLE_JOBS)  # filtered set, not raw fixture count
    service = IngestionService()

    run1 = await service.ingest_source(_make_adapter(), "example", db_session)
    assert run1.status == "success"
    assert run1.postings_fetched == expected
    assert run1.postings_new == expected
    assert run1.postings_updated == 0

    run2 = await service.ingest_source(_make_adapter(), "example", db_session)
    assert run2.status == "success"
    assert run2.postings_fetched == expected
    assert run2.postings_new == 0
    assert run2.postings_updated == expected
