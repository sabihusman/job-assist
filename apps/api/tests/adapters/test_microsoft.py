"""Tests for the Microsoft Careers adapter (directive B).

Unit tests (no DB, no network)
  TestTitleFilter                 — allow-list + exclusion regex for the
                                     program-manager family; product-manager
                                     family delegates to title_filter.py
  TestSeniorityRoleFamilyEnum     — real Microsoft title strings always map to
                                     a valid SeniorityLevel/RoleFamily member
                                     (mirrors the Wellfound associate_pm/apm
                                     regression pattern)
  TestFetchPostings               — pagination, cross-query dedup, the
                                     empty-page pagination cap, 404 handling,
                                     timeout propagation, rate limiting
  TestNormalize                   — field mapping off a pinned position_details
                                     shape (Phase 1 live-trace fixture)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from job_assist.adapters.base import HandleNotFoundError, RawPosting
from job_assist.adapters.microsoft import (
    _MAX_CONSECUTIVE_EMPTY_PAGES,
    _SEED_QUERIES,
    MicrosoftCareersAdapter,
    _should_keep_msft_title,
)

# ── Title filter ─────────────────────────────────────────────────────────────


class TestTitleFilter:
    @pytest.mark.parametrize(
        "title",
        [
            "Principal Product Manager",
            "Senior Product Manager, M365 Core",
            "Product Manager II",
            "Product Manager",
            "Principal Technical Program Manager",
            "Senior Technical Program Manager - Azure Storage Platforms",
            "Technical Program Manager",
            "Program Manager",
            "Program Manager, Sales Onboarding Readiness",
        ],
    )
    def test_keeps_target_titles(self, title: str) -> None:
        assert _should_keep_msft_title(title) is True

    @pytest.mark.parametrize(
        "title",
        [
            "Product Marketing Manager",
            "Product Designer",
            "Data Center Program Manager",
            "Data Center Technical Program Manager",
            "Critical Environment Program Manager - Business Operations",
            "Supply Chain Program Manager",
            "Sales Operations Program Manager",
            "Legal Operations Program Manager",
            "Compliance Program Manager",
            "Business Program Manager",
            "Group Accounting Program Manager",
            "Software Engineer",
            "",
            None,
        ],
    )
    def test_excludes_non_target_titles(self, title: str | None) -> None:
        assert _should_keep_msft_title(title) is False


# ── Seniority / role-family — real enum membership ────────────────────────────


class TestSeniorityRoleFamilyEnum:
    @pytest.mark.parametrize(
        ("raw_title", "expected_seniority", "expected_family"),
        [
            ("Principal Product Manager", "principal_pm", "product_management"),
            ("Senior Product Manager, M365 Core", "senior_pm", "product_management"),
            ("Product Manager II", "pm", "product_management"),
            ("Principal Technical Program Manager", "principal_pm", "program_management"),
            (
                "Senior Technical Program Manager - Azure Storage Platforms",
                "senior_pm",
                "program_management",
            ),
            ("Program Manager", "unknown", "program_management"),
            ("Technical Program Manager", "unknown", "program_management"),
        ],
    )
    def test_normalize_maps_to_valid_enum_members(
        self, raw_title: str, expected_seniority: str, expected_family: str
    ) -> None:
        """Bestiary-style regression: every emitted seniority/role_family value
        MUST be a real Postgres enum member, or the INSERT raises and fails the
        whole ingest_run (see wellfound.py's associate_pm/apm bug)."""
        from job_assist.db.enums import RoleFamily, SeniorityLevel

        adapter = MicrosoftCareersAdapter(client=AsyncMock(spec=httpx.AsyncClient))
        raw = RawPosting(source_job_id="999", raw_payload=_detail_fields(999, raw_title))
        norm = adapter.normalize(raw, "Microsoft")

        assert norm.seniority_level == expected_seniority
        assert norm.role_family == expected_family
        # The real guard — membership, not just the literal comparison above.
        assert norm.seniority_level in {m.value for m in SeniorityLevel}
        assert norm.role_family in {m.value for m in RoleFamily}


# ── Fixtures / mock plumbing ──────────────────────────────────────────────────


def _search_payload(positions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": 200, "data": {"positions": positions, "count": len(positions)}}


def _detail_fields(position_id: int, name: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": position_id,
        "name": name,
        "locations": ["United States, Washington, Redmond"],
        "standardizedLocations": ["Redmond, WA, US"],
        "postedTs": 1774374801,
        "department": "Product Management",
        "jobDescription": "<p>Great role.</p>",
        "workLocationOption": "onsite",
        "positionUrl": f"/careers/job/{position_id}",
        "publicUrl": f"https://apply.careers.microsoft.com/careers/job/{position_id}",
    }
    fields.update(overrides)
    return fields


def _detail_payload(fields: dict[str, Any]) -> dict[str, Any]:
    return {"status": 200, "data": fields}


def _mock_response(payload: dict[str, Any] | None, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    resp.request = MagicMock(url="https://apply.careers.microsoft.com/api/pcsx/search")
    return resp


def _make_adapter(get_side_effect: Any, rate_limit_seconds: float = 0.0) -> MicrosoftCareersAdapter:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=get_side_effect)
    return MicrosoftCareersAdapter(client=mock_client, rate_limit_seconds=rate_limit_seconds)


# ── fetch_postings: pagination, dedup, filtering ──────────────────────────────


# One small page per seed query (< page size 10, so pagination naturally stops
# after page 1). Ids 1 and 3 are deliberately repeated across queries to
# exercise cross-query dedup; ids 2/4/5 are non-target titles that must be
# dropped by the title filter before ever reaching a detail fetch.
_LISTING_BY_QUERY: dict[str, list[dict[str, Any]]] = {
    "product manager": [
        {"id": 1, "name": "Principal Product Manager"},
        {"id": 2, "name": "Product Marketing Manager"},
    ],
    "technical program manager": [
        {"id": 3, "name": "Senior Technical Program Manager"},
        {"id": 4, "name": "Data Center Technical Program Manager"},
    ],
    "program manager": [
        {"id": 3, "name": "Senior Technical Program Manager"},
        {"id": 5, "name": "Compliance Program Manager"},
        {"id": 6, "name": "Program Manager"},
    ],
    "product manager 1": [
        {"id": 1, "name": "Principal Product Manager"},
    ],
}

_DETAILS_BY_ID: dict[int, dict[str, Any]] = {
    1: _detail_fields(1, "Principal Product Manager"),
    3: _detail_fields(3, "Senior Technical Program Manager", workLocationOption="hybrid"),
    6: _detail_fields(6, "Program Manager", locations=["Remote"], workLocationOption="remote"),
}


async def _basic_get_side_effect(url: str, params: dict[str, Any] | None = None) -> MagicMock:
    params = params or {}
    if url.endswith("/api/pcsx/search"):
        query = params.get("query")
        start = params.get("start", 0)
        positions = _LISTING_BY_QUERY.get(query, []) if start == 0 else []
        return _mock_response(_search_payload(positions))
    if url.endswith("/api/pcsx/position_details"):
        position_id = params.get("position_id")
        fields = _DETAILS_BY_ID.get(position_id)
        if fields is None:
            return _mock_response(None, status_code=404)
        return _mock_response(_detail_payload(fields))
    raise AssertionError(f"unexpected URL {url}")


class TestFetchPostings:
    async def test_dedups_across_queries_and_filters_titles(self) -> None:
        adapter = _make_adapter(_basic_get_side_effect)
        raws = await adapter.fetch_postings("microsoft")

        ids = {r.source_job_id for r in raws}
        assert ids == {"1", "3", "6"}  # excluded (2, 4, 5) never fetched; dupes collapsed
        assert len(raws) == 3

    async def test_kept_raw_payload_is_the_detail_data_object(self) -> None:
        adapter = _make_adapter(_basic_get_side_effect)
        raws = await adapter.fetch_postings("microsoft")
        by_id = {r.source_job_id: r for r in raws}
        assert by_id["1"].raw_payload["name"] == "Principal Product Manager"

    async def test_404_on_first_listing_call_raises_handle_not_found(self) -> None:
        async def side_effect(url: str, params: dict[str, Any] | None = None) -> MagicMock:
            if url.endswith("/api/pcsx/search"):
                return _mock_response(None, status_code=404)
            raise AssertionError("should not reach detail fetch")

        adapter = _make_adapter(side_effect)
        with pytest.raises(HandleNotFoundError) as exc_info:
            await adapter.fetch_postings("microsoft")
        assert exc_info.value.ats == "microsoft"
        assert exc_info.value.handle == "microsoft"

    async def test_per_job_404_is_silently_skipped(self) -> None:
        """A posting deleted between listing and detail fetch — silent skip,
        not a listing-level failure (Bestiary 5.9 per-job convention)."""

        async def side_effect(url: str, params: dict[str, Any] | None = None) -> MagicMock:
            params = params or {}
            if url.endswith("/api/pcsx/search"):
                query = params.get("query")
                start = params.get("start", 0)
                if query == "product manager" and start == 0:
                    return _mock_response(
                        _search_payload([{"id": 1, "name": "Principal Product Manager"}])
                    )
                return _mock_response(_search_payload([]))
            return _mock_response(None, status_code=404)  # every detail fetch 404s

        adapter = _make_adapter(side_effect)
        raws = await adapter.fetch_postings("microsoft")
        assert raws == []

    async def test_empty_page_cap_stops_pagination_before_a_later_real_hit(self) -> None:
        """Three consecutive zero-keep FULL pages stop that query's pagination —
        a real posting sitting on page 4 (start=30) must never be fetched."""
        noise_page = [{"id": 100 + i, "name": "Software Engineer"} for i in range(10)]
        real_hit_page = [{"id": 200, "name": "Product Manager"}]
        calls: list[tuple[str, int]] = []

        async def side_effect(url: str, params: dict[str, Any] | None = None) -> MagicMock:
            params = params or {}
            if url.endswith("/api/pcsx/search"):
                query = params.get("query")
                start = params.get("start", 0)
                if query == "product manager":
                    calls.append((query, start))
                    if start in (0, 10, 20):
                        return _mock_response(_search_payload(noise_page))
                    if start == 30:
                        return _mock_response(_search_payload(real_hit_page))
                return _mock_response(_search_payload([]))  # other 3 seed queries: instant stop
            raise AssertionError("no keeps expected — detail should never be called")

        adapter = _make_adapter(side_effect)
        raws = await adapter.fetch_postings("microsoft")

        assert raws == []
        assert (
            "product manager",
            30,
        ) not in calls, "pagination must stop after 3 consecutive empty pages"
        assert len(calls) == _MAX_CONSECUTIVE_EMPTY_PAGES

    async def test_timeout_propagates_not_swallowed(self) -> None:
        """Bestiary 5.19: a retry-exhausted timeout PROPAGATES, never swallowed
        as an empty list (which would look like a genuinely empty board and
        falsely close every live Microsoft posting via stale-detection)."""
        adapter = _make_adapter(_basic_get_side_effect)
        adapter._get = AsyncMock(side_effect=httpx.ReadTimeout("slow"))  # type: ignore[method-assign]
        with pytest.raises(httpx.TimeoutException):
            await adapter.fetch_postings("microsoft")

    async def test_rate_limit_sleeps_between_every_call(self) -> None:
        adapter = _make_adapter(_basic_get_side_effect, rate_limit_seconds=1.0)
        with patch("job_assist.adapters.microsoft.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await adapter.fetch_postings("microsoft")
        assert sleep_mock.await_count > 0
        sleep_mock.assert_awaited_with(1.0)

    async def test_default_rate_limit_is_one_second(self) -> None:
        adapter = MicrosoftCareersAdapter(client=AsyncMock(spec=httpx.AsyncClient))
        assert adapter._rate_limit_seconds == 1.0


# ── normalize() ────────────────────────────────────────────────────────────────


class TestNormalize:
    def _adapter(self) -> MicrosoftCareersAdapter:
        return MicrosoftCareersAdapter(client=AsyncMock(spec=httpx.AsyncClient))

    def test_maps_core_fields(self) -> None:
        fields = _detail_fields(
            1,
            "Principal Product Manager",
            jobDescription=(
                "<p>Own the roadmap.</p>"
                "<p>Product Management IC5 - The typical base pay range for this "
                "role is USD $142,800 - $274,800 per year.</p>"
            ),
        )
        raw = RawPosting(source_job_id="1", raw_payload=fields)
        norm = self._adapter().normalize(raw, "Microsoft")

        assert norm.raw_title == "Principal Product Manager"
        assert norm.canonical_company_name == "Microsoft"
        assert norm.ats == "microsoft"
        assert norm.source_job_id == "1"
        assert norm.parser_version == "microsoft-v1"
        assert "<" not in norm.jd_text
        assert "Own the roadmap" in norm.jd_text

    def test_salary_parsed_from_free_text_description(self) -> None:
        fields = _detail_fields(
            1,
            "Principal Product Manager",
            jobDescription="Product Management IC5 - USD $142,800 - $274,800 per year.",
        )
        raw = RawPosting(source_job_id="1", raw_payload=fields)
        norm = self._adapter().normalize(raw, "Microsoft")

        assert norm.salary_min == 142_800
        assert norm.salary_max == 274_800
        assert norm.salary_currency == "USD"
        assert norm.salary_period == "annual"

    def test_no_salary_in_description_leaves_fields_none(self) -> None:
        fields = _detail_fields(
            3, "Senior Technical Program Manager", jobDescription="<p>No comp here.</p>"
        )
        raw = RawPosting(source_job_id="3", raw_payload=fields)
        norm = self._adapter().normalize(raw, "Microsoft")

        assert norm.salary_min is None
        assert norm.salary_max is None
        assert norm.salary_period == "unknown"

    @pytest.mark.parametrize(
        ("work_location_option", "expected_remote_type"),
        [("onsite", "onsite"), ("hybrid", "hybrid"), ("remote", "remote")],
    )
    def test_remote_type_from_work_location_option(
        self, work_location_option: str, expected_remote_type: str
    ) -> None:
        fields = _detail_fields(1, "Product Manager", workLocationOption=work_location_option)
        raw = RawPosting(source_job_id="1", raw_payload=fields)
        norm = self._adapter().normalize(raw, "Microsoft")
        assert norm.remote_type == expected_remote_type

    def test_posted_at_parsed_from_unix_seconds(self) -> None:
        fields = _detail_fields(1, "Product Manager", postedTs=1774374801)
        raw = RawPosting(source_job_id="1", raw_payload=fields)
        norm = self._adapter().normalize(raw, "Microsoft")
        assert norm.posted_at is not None
        assert norm.posted_at.year == 2026

    def test_source_and_apply_urls(self) -> None:
        fields = _detail_fields(42, "Product Manager")
        raw = RawPosting(source_job_id="42", raw_payload=fields)
        norm = self._adapter().normalize(raw, "Microsoft")

        assert norm.source_url == "https://apply.careers.microsoft.com/careers/job/42"
        assert norm.apply_url == (
            "https://apply.careers.microsoft.com/careers/apply?pid=42&domain=microsoft.com"
        )

    def test_peek_title_matches_normalize_title_source(self) -> None:
        fields = _detail_fields(1, "Principal Product Manager")
        raw = RawPosting(source_job_id="1", raw_payload=fields)
        adapter = self._adapter()
        assert adapter.peek_title(raw) == adapter.normalize(raw, "Microsoft").raw_title


def test_seed_queries_cover_all_four_directive_titles() -> None:
    assert _SEED_QUERIES == (
        "product manager",
        "technical program manager",
        "program manager",
        "product manager 1",
    )
