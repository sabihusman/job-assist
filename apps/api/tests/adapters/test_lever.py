"""Tests for the Lever adapter.

Unit tests (no DB, no network)
  test_normalize_first_posting          — descriptionPlain wins, hybrid mapped
  test_normalize_html_fallback          — descriptionPlain empty → strip HTML
  test_normalize_remote_workplace_type  — workplaceType="remote" overrides loc
  test_normalize_on_site_alias          — "on-site" maps to "onsite"
  test_normalize_unspecified_falls_back — workplaceType="unspecified" → loc-derived
  test_fetch_postings_returns_all       — adapter.fetch_postings unwraps the list
  test_404_raises_handle_not_found      — 404 → HandleNotFoundError (Bestiary 5.9)
  test_non_200_non_404_still_returns_empty — other 4xx falls through to []
  test_fetch_postings_handles_non_list  — malformed payload → empty list
  test_normalize_seniority_role_family  — heuristics carry over via shared module

Integration tests (require TEST_DATABASE_URL)
  test_idempotency_counts               — second run = 0 new, N updated
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from job_assist.adapters.base import RawPosting
from job_assist.adapters.lever import LeverAdapter

_FIXTURE_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "lever_ramp.json"
_FIXTURE: list[dict[str, Any]] = json.loads(_FIXTURE_PATH.read_text())

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_adapter(
    payload: list[dict[str, Any]] | None = None,
    status_code: int = 200,
) -> LeverAdapter:
    """Return a LeverAdapter backed by a mock client returning *payload*."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = payload if payload is not None else _FIXTURE

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return LeverAdapter(client=mock_client)


def _raw(index: int) -> RawPosting:
    """Wrap a fixture entry as a RawPosting."""
    job = _FIXTURE[index]
    return RawPosting(source_job_id=str(job["id"]), raw_payload=job)


# ── Unit tests — normalization ────────────────────────────────────────────────


class TestNormalize:
    def test_descriptionPlain_wins(self) -> None:
        """descriptionPlain is preferred over the HTML description field."""
        norm = _make_adapter().normalize(_raw(0), "Ramp")
        assert "core payments rails" in norm.jd_text
        assert "<" not in norm.jd_text, "HTML must not leak into jd_text"
        assert "HTML fallback" not in norm.jd_text

    def test_html_fallback_when_plain_empty(self) -> None:
        """When descriptionPlain is empty, fall back to stripping HTML description."""
        norm = _make_adapter().normalize(_raw(1), "Ramp")
        assert "<" not in norm.jd_text
        assert "Lead Product Manager, Risk" in norm.jd_text
        assert "Cross-functional partnership" in norm.jd_text

    def test_hybrid_workplace_type_mapped(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Ramp")
        assert norm.remote_type == "hybrid"

    def test_remote_workplace_type_mapped(self) -> None:
        norm = _make_adapter().normalize(_raw(1), "Ramp")
        assert norm.remote_type == "remote"

    def test_on_site_alias_mapped(self) -> None:
        """Lever's "on-site" → our "onsite" enum value."""
        norm = _make_adapter().normalize(_raw(2), "Ramp")
        assert norm.remote_type == "onsite"

    def test_unspecified_falls_back_to_location_scan(self) -> None:
        """workplaceType="unspecified" → keyword-scan derives remote from "Remote"."""
        norm = _make_adapter().normalize(_raw(3), "Ramp")
        assert norm.remote_type == "remote"

    def test_all_locations_parsed(self) -> None:
        """categories.allLocations (list) is preferred over categories.location."""
        norm = _make_adapter().normalize(_raw(1), "Ramp")
        # Two entries in allLocations — both should appear in locations_normalized.
        assert len(norm.locations_normalized) >= 2

    def test_seniority_role_family_via_shared_helpers(self) -> None:
        """The shared seniority/role-family heuristics apply identically to Lever titles."""
        adapter = _make_adapter()

        senior_pm = adapter.normalize(_raw(0), "Ramp")
        assert senior_pm.seniority_level == "senior_pm"
        assert senior_pm.role_family == "product_management"

        lead_pm = adapter.normalize(_raw(1), "Ramp")
        assert lead_pm.seniority_level == "lead_pm"
        assert lead_pm.role_family == "product_management"

        apm = adapter.normalize(_raw(2), "Ramp")
        assert apm.seniority_level == "apm"
        assert apm.role_family == "product_management"

        # Non-PM role: catch-all branches in both heuristics.
        non_pm = adapter.normalize(_raw(3), "Ramp")
        assert non_pm.seniority_level == "unknown"
        assert non_pm.role_family == "other"

    def test_source_and_apply_urls(self) -> None:
        norm = _make_adapter().normalize(_raw(0), "Ramp")
        assert norm.source_url.startswith("https://jobs.lever.co/ramp/")
        assert norm.apply_url is not None
        assert norm.apply_url.endswith("/apply")

    def test_posted_at_from_epoch_millis(self) -> None:
        """createdAt is milliseconds since epoch — parsed to a UTC datetime."""
        norm = _make_adapter().normalize(_raw(0), "Ramp")
        assert norm.posted_at is not None
        assert norm.posted_at.year >= 2024


# ── Unit tests — fetch_postings ───────────────────────────────────────────────


class TestFetchPostings:
    async def test_returns_all_postings(self) -> None:
        adapter = _make_adapter()
        raws = await adapter.fetch_postings("ramp")
        assert len(raws) == len(_FIXTURE)
        assert all(isinstance(r, RawPosting) for r in raws)
        assert raws[0].source_job_id == _FIXTURE[0]["id"]

    async def test_404_raises_handle_not_found(self) -> None:
        """Bestiary 5.9: 404 on the listing call surfaces as
        HandleNotFoundError so the orchestrator can record a distinct
        ingest_run_status instead of conflating with generic empty."""
        from job_assist.adapters.base import HandleNotFoundError

        adapter = _make_adapter(status_code=404)
        with pytest.raises(HandleNotFoundError) as exc_info:
            await adapter.fetch_postings("nonexistent")
        assert exc_info.value.ats == "lever"
        assert exc_info.value.handle == "nonexistent"
        assert "lever.co" in exc_info.value.url

    async def test_non_200_non_404_still_returns_empty(self) -> None:
        """Only 404 raises; other non-200 keeps the historical silent
        return so transient upstream errors don't fail the whole batch.
        (5xx is retried at the tenacity-wrapped ``_get`` layer; this
        test covers the 4xx-non-404 branch.)"""
        adapter = _make_adapter(status_code=418)
        assert await adapter.fetch_postings("teapot") == []

    async def test_handles_non_list_payload(self) -> None:
        """If the response is not a list (e.g. error envelope), return []."""
        adapter = _make_adapter(payload={"error": "boom"})  # type: ignore[arg-type]
        assert await adapter.fetch_postings("ramp") == []

    async def test_skips_entries_without_id(self) -> None:
        bad: list[dict[str, Any]] = [{"text": "no id"}, _FIXTURE[0]]
        adapter = _make_adapter(payload=bad)
        raws = await adapter.fetch_postings("ramp")
        assert len(raws) == 1
        assert raws[0].source_job_id == _FIXTURE[0]["id"]


# ── Integration tests ─────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_idempotency_counts(db_session: Any) -> None:
    """Re-ingesting identical Lever data must report all-updates on run 2."""
    from job_assist.services.ingestion import IngestionService

    expected = len(_FIXTURE)
    service = IngestionService()

    run1 = await service.ingest_source(_make_adapter(), "ramp", db_session)
    assert run1.status == "success"
    assert run1.postings_fetched == expected
    assert run1.postings_new == expected
    assert run1.postings_updated == 0

    run2 = await service.ingest_source(_make_adapter(), "ramp", db_session)
    assert run2.status == "success"
    assert run2.postings_fetched == expected
    assert run2.postings_new == 0
    assert run2.postings_updated == expected
