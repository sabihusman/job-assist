"""Tests for the Workday ATS adapter (PR #33).

Two slices:
  * Sync (no DB, no network) — covers URL construction, response
    parsing, pagination, retry, normalize() field mapping.
  * Schema — confirms the migration's `adapter_config` JSONB column
    accepts JSON.

Network tests use httpx's MockTransport so no real Workday tenants
are touched in CI. Two captured fixture responses sit at
``apps/api/tests/fixtures/workday/`` to keep the test inputs honest
about the real shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from job_assist.adapters.base import RawPosting
from job_assist.adapters.workday import (
    WorkdayAdapter,
    _build_detail_url,
    _build_jobs_url,
    _extract_department_team,
    _extract_req_id,
    _parse_posted_on,
    _site_from_path,
    detect_workday_url,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "workday"
_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _load(name: str) -> dict[str, Any]:
    with (_FIXTURES / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# ── URL helpers ─────────────────────────────────────────────────────────


def test_build_jobs_url_uses_tenant_wd_and_site() -> None:
    assert (
        _build_jobs_url("jpmc", "wd5", "External")
        == "https://jpmc.wd5.myworkdayjobs.com/wday/cxs/jpmc/External/jobs"
    )


def test_build_detail_url_prefixes_external_path() -> None:
    url = _build_detail_url("jpmc", "wd5", "External", "/job/x/Senior-PM_R-1")
    assert url == ("https://jpmc.wd5.myworkdayjobs.com/wday/cxs/jpmc/External/job/x/Senior-PM_R-1")


def test_build_detail_url_handles_missing_leading_slash() -> None:
    url = _build_detail_url("jpmc", "wd5", "External", "job/x/PM_R-1")
    assert url.endswith("/External/job/x/PM_R-1")


# ── detect_workday_url ──────────────────────────────────────────────────


def test_detect_workday_url_matches_canonical_form() -> None:
    parts = detect_workday_url("https://jpmc.wd5.myworkdayjobs.com/External")
    assert parts == {"tenant": "jpmc", "wd_number": "wd5", "site": "External"}


def test_detect_workday_url_extracts_site_after_locale() -> None:
    parts = detect_workday_url(
        "https://capitalone.wd1.myworkdayjobs.com/en-US/Capital_One/job/foo",
    )
    assert parts == {"tenant": "capitalone", "wd_number": "wd1", "site": "Capital_One"}


def test_detect_workday_url_defaults_site_when_undetectable() -> None:
    parts = detect_workday_url("https://example.wd9.myworkdayjobs.com")
    assert parts == {"tenant": "example", "wd_number": "wd9", "site": "External"}


def test_detect_workday_url_returns_none_for_non_workday() -> None:
    assert detect_workday_url("https://boards.greenhouse.io/stripe") is None
    assert detect_workday_url("") is None


def test_site_from_path_extracts_segment_after_locale() -> None:
    assert _site_from_path("/en-US/External") == "External"
    assert _site_from_path("/External") == "External"
    assert _site_from_path("/fr-FR/MyCustomSite/job/123") == "MyCustomSite"


# ── _parse_posted_on ────────────────────────────────────────────────────


def test_parse_posted_on_today_returns_now() -> None:
    dt = _parse_posted_on("Posted Today")
    assert dt is not None


def test_parse_posted_on_days_ago_returns_offset() -> None:
    from datetime import UTC, datetime

    dt = _parse_posted_on("Posted 5 Days Ago")
    assert dt is not None
    delta = datetime.now(tz=UTC) - dt
    assert 4 <= delta.days <= 6


def test_parse_posted_on_30_plus_days_returns_30() -> None:
    from datetime import UTC, datetime

    dt = _parse_posted_on("Posted 30+ Days Ago")
    assert dt is not None
    delta = datetime.now(tz=UTC) - dt
    assert 29 <= delta.days <= 31


def test_parse_posted_on_unparseable_returns_none() -> None:
    assert _parse_posted_on(None) is None
    assert _parse_posted_on("") is None
    assert _parse_posted_on("recently") is None


# ── _extract_req_id / _extract_department_team ──────────────────────────


def test_extract_req_id_prefers_detail_jobReqId() -> None:
    raw_job = {"bulletFields": ["LIST-1"]}
    raw_detail = {"jobPostingInfo": {"jobReqId": "DETAIL-1"}}
    assert _extract_req_id(raw_job, raw_detail) == "DETAIL-1"


def test_extract_req_id_falls_back_to_bullet_fields() -> None:
    raw_job = {"bulletFields": ["R-123"]}
    raw_detail = {"jobPostingInfo": {}}
    assert _extract_req_id(raw_job, raw_detail) == "R-123"


def test_extract_req_id_returns_none_when_missing() -> None:
    assert _extract_req_id({}, {}) is None


def test_extract_department_team_reads_jobFamily() -> None:
    detail = {"jobPostingInfo": {"jobFamily": "Engineering"}}
    dept, team = _extract_department_team(detail)
    assert dept == "Engineering"
    assert team is None


def test_extract_department_team_reads_department_when_present() -> None:
    detail = {"jobPostingInfo": {"department": "Risk", "jobFamily": "Eng"}}
    dept, _ = _extract_department_team(detail)
    assert dept == "Risk"  # department wins over jobFamily


def test_extract_department_team_returns_none_when_missing() -> None:
    assert _extract_department_team({}) == (None, None)


# ── normalize() ─────────────────────────────────────────────────────────


def _make_adapter() -> WorkdayAdapter:
    return WorkdayAdapter(adapter_config={"wd_number": "wd5", "site": "External"})


def test_normalize_populates_basic_fields() -> None:
    raw = RawPosting(
        source_job_id="R-12345",
        raw_payload={
            "list": _load("jpmc_list_page1.json")["jobPostings"][0],
            "detail": _load("jpmc_detail_R-12345.json"),
        },
    )
    norm = _make_adapter().normalize(raw, "JPMorgan Chase")
    assert norm.raw_title == "Senior Product Manager, Wealth Platform"
    assert norm.location_raw == "New York, NY"
    assert norm.canonical_company_name == "JPMorgan Chase"
    assert norm.ats == "workday"
    assert norm.source_job_id == "R-12345"
    assert norm.parser_version == "workday-v1"


def test_normalize_strips_html_from_jd() -> None:
    raw = RawPosting(
        source_job_id="R-12345",
        raw_payload={
            "list": _load("jpmc_list_page1.json")["jobPostings"][0],
            "detail": _load("jpmc_detail_R-12345.json"),
        },
    )
    norm = _make_adapter().normalize(raw, "JPMorgan Chase")
    assert "<p>" not in norm.jd_text
    assert "Senior Product Manager" in norm.jd_text


def test_normalize_extracts_department_from_jobFamily() -> None:
    raw = RawPosting(
        source_job_id="R-12345",
        raw_payload={
            "list": _load("jpmc_list_page1.json")["jobPostings"][0],
            "detail": _load("jpmc_detail_R-12345.json"),
        },
    )
    norm = _make_adapter().normalize(raw, "JPMorgan Chase")
    assert norm.department == "Product"
    assert norm.team is None


def test_normalize_handles_missing_department_gracefully() -> None:
    raw = RawPosting(
        source_job_id="R-987654",
        raw_payload={
            "list": _load("capitalone_list_page1.json")["jobPostings"][0],
            "detail": {},
        },
    )
    norm = _make_adapter().normalize(raw, "Capital One")
    assert norm.department is None
    assert norm.team is None


def test_normalize_remote_type_hybrid_from_remoteType_field() -> None:
    raw = RawPosting(
        source_job_id="R-12345",
        raw_payload={
            "list": _load("jpmc_list_page1.json")["jobPostings"][0],
            "detail": _load("jpmc_detail_R-12345.json"),
        },
    )
    norm = _make_adapter().normalize(raw, "JPMorgan Chase")
    assert norm.remote_type == "hybrid"


def test_normalize_leaves_salary_null() -> None:
    raw = RawPosting(
        source_job_id="R-12345",
        raw_payload={
            "list": _load("jpmc_list_page1.json")["jobPostings"][0],
            "detail": _load("jpmc_detail_R-12345.json"),
        },
    )
    norm = _make_adapter().normalize(raw, "JPMorgan Chase")
    assert norm.salary_min is None
    assert norm.salary_max is None
    assert norm.salary_currency is None


def test_normalize_uses_external_url_as_source_url() -> None:
    raw = RawPosting(
        source_job_id="R-12345",
        raw_payload={
            "list": _load("jpmc_list_page1.json")["jobPostings"][0],
            "detail": _load("jpmc_detail_R-12345.json"),
        },
    )
    norm = _make_adapter().normalize(raw, "JPMorgan Chase")
    assert "jpmc.wd5.myworkdayjobs.com" in norm.source_url
    assert "R-12345" in norm.source_url


# ── fetch_postings via MockTransport ────────────────────────────────────


async def test_fetch_postings_walks_pages_until_empty() -> None:
    """Two pages of postings followed by an empty page stops pagination."""
    page1 = _load("jpmc_list_page1.json")
    detail_a = _load("jpmc_detail_R-12345.json")
    # Capital One fixture's job; reused so we don't need a 3rd fixture file.
    detail_b = {
        "jobPostingInfo": {
            "title": "Staff Engineer, Payments",
            "jobDescription": "<p>desc</p>",
            "jobReqId": "R-67890",
            "location": "Plano, TX",
            "externalUrl": "https://jpmc.wd5.myworkdayjobs.com/foo",
        }
    }
    page2: dict[str, Any] = {"jobPostings": []}

    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        if request.method == "POST":
            body = json.loads(request.content)
            if body["offset"] == 0:
                return httpx.Response(200, json=page1)
            return httpx.Response(200, json=page2)
        # GET detail — match by external path
        if "R-12345" in str(request.url):
            return httpx.Response(200, json=detail_a)
        if "R-67890" in str(request.url):
            return httpx.Response(200, json=detail_b)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with WorkdayAdapter(
        adapter_config={"wd_number": "wd5", "site": "External"},
        client=client,
    ) as adapter:
        postings = await adapter.fetch_postings("jpmc")
    await client.aclose()

    assert len(postings) == 2
    ids = sorted(p.source_job_id for p in postings)
    assert ids == ["R-12345", "R-67890"]
    # Exactly one POST request per page (2 list pages) + one GET per job.
    posts = [c for c in calls if c.startswith("POST")]
    gets = [c for c in calls if c.startswith("GET")]
    assert len(posts) == 2
    assert len(gets) == 2


async def test_fetch_postings_constructs_correct_url_and_body() -> None:
    """First POST must hit /wday/cxs/{tenant}/{site}/jobs with the right body."""
    seen_url: list[str] = []
    seen_body: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen_url.append(str(request.url))
            seen_body.append(json.loads(request.content))
            return httpx.Response(200, json={"jobPostings": []})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with WorkdayAdapter(
        adapter_config={"wd_number": "wd3", "site": "CustomSite"},
        client=client,
    ) as adapter:
        await adapter.fetch_postings("acmebank")
    await client.aclose()

    assert seen_url[0] == (
        "https://acmebank.wd3.myworkdayjobs.com/wday/cxs/acmebank/CustomSite/jobs"
    )
    body = seen_body[0]
    assert body == {
        "appliedFacets": {},
        "limit": 50,
        "offset": 0,
        "searchText": "",
    }


async def test_fetch_postings_handles_429_with_retry() -> None:
    """429 on the first attempt is retried via tenacity."""
    page = _load("jpmc_list_page1.json")
    detail = _load("jpmc_detail_R-12345.json")
    attempts = {"posts": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            attempts["posts"] += 1
            if attempts["posts"] == 1:
                return httpx.Response(429)
            if attempts["posts"] == 2:
                return httpx.Response(200, json=page)
            return httpx.Response(200, json={"jobPostings": []})
        return httpx.Response(200, json=detail)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with WorkdayAdapter(
        adapter_config={"wd_number": "wd5", "site": "External"},
        client=client,
    ) as adapter:
        postings = await adapter.fetch_postings("jpmc")
    await client.aclose()

    assert attempts["posts"] >= 2
    assert len(postings) >= 1


# ── Schema / DB ─────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_target_company_can_hold_adapter_config(db_session: Any) -> None:
    """The migration's JSONB column accepts a dict and reads it back."""
    from job_assist.db.models.target_company import TargetCompany

    tc = TargetCompany(
        name="JPMorgan Chase (test)",
        tier=1,
        ats="workday",
        ats_handle="jpmc",
        adapter_config={"wd_number": "wd5", "site": "External"},
    )
    db_session.add(tc)
    await db_session.commit()

    fetched = (
        await db_session.execute(
            sa.text(
                "SELECT adapter_config FROM target_company WHERE ats_handle='jpmc'",
            )
        )
    ).scalar_one()
    assert fetched == {"wd_number": "wd5", "site": "External"}
