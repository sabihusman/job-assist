"""Tests for the iCIMS ATS adapter (PR #55).

Mirrors ``tests/test_workday_adapter.py`` exactly in shape — sync tests
dominate (URL builders, parsers, normalize() field mapping), and
``httpx.MockTransport`` carries the fetch-path tests so no real iCIMS
career site is touched in CI.

Bestiary note: fixtures at ``tests/fixtures/icims/*.html`` are
HAND-AUTHORED — see the comment at the top of each file. The first
real iCIMS handle ingested after merge is the truth check.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from job_assist.adapters.base import RawPosting
from job_assist.adapters.icims import (
    ICIMSAdapter,
    _build_detail_url,
    _build_listing_url,
    _extract_department_team_from_jsonld,
    _extract_jsonld,
    _extract_listing_rows,
    _extract_location_from_jsonld,
    _extract_salary_from_jsonld,
    _parse_iso_datetime,
    detect_icims_url,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "icims"


def _load(name: str) -> str:
    with (_FIXTURES / name).open("r", encoding="utf-8") as f:
        return f.read()


# ── URL helpers ─────────────────────────────────────────────────────────


def test_build_listing_url_includes_offset_and_iframe_flag() -> None:
    url = _build_listing_url("https://careers-acmecorp.icims.com", 0)
    assert url.startswith("https://careers-acmecorp.icims.com/jobs/search?")
    assert "in_iframe=1" in url
    assert "offset=0" in url


def test_build_listing_url_increments_offset() -> None:
    url = _build_listing_url("https://careers-acmecorp.icims.com", 25)
    assert "offset=25" in url


def test_build_detail_url_concatenates_id_and_slug() -> None:
    url = _build_detail_url(
        "https://careers-acmecorp.icims.com",
        "12345",
        "senior-product-manager-platform",
    )
    assert url == (
        "https://careers-acmecorp.icims.com/jobs/12345/senior-product-manager-platform/job"
    )


def test_build_detail_url_handles_trailing_slash_on_careers_url() -> None:
    url = _build_detail_url("https://careers-acmecorp.icims.com/", "1", "slug")
    assert url == "https://careers-acmecorp.icims.com/jobs/1/slug/job"


# ── detect_icims_url ────────────────────────────────────────────────────


def test_detect_icims_url_matches_careers_prefix() -> None:
    assert detect_icims_url("https://careers-acmecorp.icims.com") == "acmecorp"


def test_detect_icims_url_matches_bare_subdomain() -> None:
    assert detect_icims_url("https://acmecorp.icims.com/jobs/search") == "acmecorp"


def test_detect_icims_url_returns_none_for_non_icims() -> None:
    assert detect_icims_url("https://boards.greenhouse.io/acmecorp") is None
    assert detect_icims_url("") is None


def test_detect_icims_url_is_case_insensitive_on_host() -> None:
    assert detect_icims_url("https://CAREERS-ACME.icims.com") == "acme"


# ── _extract_listing_rows ───────────────────────────────────────────────


def test_extract_listing_rows_finds_three_unique_jobs() -> None:
    """Fixture has 4 detail-shaped anchors + 1 duplicate + 1 non-detail.
    Parser must return exactly 3 unique rows."""
    rows = _extract_listing_rows(_load("acmecorp_listing.html"))
    ids = sorted(r["source_job_id"] for r in rows)
    assert ids == ["12345", "12346", "12347"]


def test_extract_listing_rows_captures_titles() -> None:
    rows = _extract_listing_rows(_load("acmecorp_listing.html"))
    by_id = {r["source_job_id"]: r for r in rows}
    assert by_id["12345"]["raw_title"] == "Senior Product Manager, Platform"
    assert by_id["12347"]["raw_title"] == "Lead Product Manager, Growth"


def test_extract_listing_rows_captures_slugs() -> None:
    rows = _extract_listing_rows(_load("acmecorp_listing.html"))
    by_id = {r["source_job_id"]: r for r in rows}
    assert by_id["12345"]["slug"] == "senior-product-manager-platform"


def test_extract_listing_rows_returns_empty_on_empty_html() -> None:
    assert _extract_listing_rows("") == []


def test_extract_listing_rows_ignores_non_detail_anchors() -> None:
    html = '<html><body><a href="/about">About</a><a href="/contact">Contact</a></body></html>'
    assert _extract_listing_rows(html) == []


# ── _extract_jsonld ─────────────────────────────────────────────────────


def test_extract_jsonld_pulls_jobposting_object() -> None:
    jsonld = _extract_jsonld(_load("acmecorp_detail_12345.html"))
    assert jsonld is not None
    assert jsonld["@type"] == "JobPosting"
    assert jsonld["title"] == "Senior Product Manager, Platform"


def test_extract_jsonld_walks_graph_wrapper() -> None:
    """Detail 12347 wraps the JobPosting inside an @graph list."""
    jsonld = _extract_jsonld(_load("acmecorp_detail_12347.html"))
    assert jsonld is not None
    assert jsonld["title"] == "Lead Product Manager, Growth"


def test_extract_jsonld_returns_none_when_absent() -> None:
    html = "<html><body><h1>No JSON-LD here</h1></body></html>"
    assert _extract_jsonld(html) is None


def test_extract_jsonld_returns_none_on_malformed_json() -> None:
    html = '<html><body><script type="application/ld+json">{not json</script></body></html>'
    assert _extract_jsonld(html) is None


# ── _extract_location_from_jsonld ───────────────────────────────────────


def test_extract_location_concatenates_address_parts() -> None:
    jsonld = _extract_jsonld(_load("acmecorp_detail_12345.html"))
    assert jsonld is not None
    loc = _extract_location_from_jsonld(jsonld)
    assert loc == "New York, NY, US"


def test_extract_location_handles_missing_country() -> None:
    """Detail 12347's address omits addressCountry — parser must still
    return the parts it has."""
    jsonld = _extract_jsonld(_load("acmecorp_detail_12347.html"))
    assert jsonld is not None
    loc = _extract_location_from_jsonld(jsonld)
    assert loc == "San Francisco, CA"


def test_extract_location_returns_none_when_no_address() -> None:
    assert _extract_location_from_jsonld({}) is None
    assert _extract_location_from_jsonld({"jobLocation": "freeform string"}) is None


# ── _extract_salary_from_jsonld ─────────────────────────────────────────


def test_extract_salary_pulls_quantitative_value() -> None:
    jsonld = _extract_jsonld(_load("acmecorp_detail_12345.html"))
    assert jsonld is not None
    smin, smax, currency = _extract_salary_from_jsonld(jsonld)
    assert smin == 180_000
    assert smax == 240_000
    assert currency == "USD"


def test_extract_salary_returns_nones_when_basesalary_missing() -> None:
    """Detail 12346 omits baseSalary."""
    jsonld = _extract_jsonld(_load("acmecorp_detail_12346.html"))
    assert jsonld is not None
    smin, smax, currency = _extract_salary_from_jsonld(jsonld)
    assert smin is None
    assert smax is None
    assert currency is None


def test_extract_salary_rejects_values_below_1000() -> None:
    """Filters out hourly-shaped values that look implausible as annual."""
    fake_jsonld = {
        "baseSalary": {
            "currency": "USD",
            "value": {"minValue": 50, "maxValue": 75},
        }
    }
    smin, smax, _ = _extract_salary_from_jsonld(fake_jsonld)
    assert smin is None
    assert smax is None


# ── _extract_department_team_from_jsonld ────────────────────────────────


def test_extract_department_team_reads_industry() -> None:
    jsonld = _extract_jsonld(_load("acmecorp_detail_12345.html"))
    assert jsonld is not None
    dept, team = _extract_department_team_from_jsonld(jsonld)
    assert dept == "Product"
    assert team is None


def test_extract_department_team_returns_none_when_missing() -> None:
    assert _extract_department_team_from_jsonld({}) == (None, None)


# ── _parse_iso_datetime ─────────────────────────────────────────────────


def test_parse_iso_datetime_date_only() -> None:
    dt = _parse_iso_datetime("2026-05-20")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 5 and dt.day == 20
    assert dt.tzinfo is not None


def test_parse_iso_datetime_full_iso_with_z() -> None:
    dt = _parse_iso_datetime("2026-05-20T14:30:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.hour == 14
    assert dt.tzinfo is not None


def test_parse_iso_datetime_unparseable_returns_none() -> None:
    assert _parse_iso_datetime(None) is None  # type: ignore[arg-type]
    assert _parse_iso_datetime("") is None
    assert _parse_iso_datetime("recently") is None


# ── normalize() ─────────────────────────────────────────────────────────


def _make_adapter() -> ICIMSAdapter:
    return ICIMSAdapter()


def _make_raw(detail_filename: str, source_job_id: str, slug: str) -> RawPosting:
    detail_html = _load(detail_filename)
    jsonld = _extract_jsonld(detail_html)
    return RawPosting(
        source_job_id=source_job_id,
        raw_payload={
            "listing_row": {
                "source_job_id": source_job_id,
                "slug": slug,
                "raw_title": "Listing-row title",
            },
            "detail_html": detail_html,
            "jsonld": jsonld or {},
            "careers_url": "https://careers-acmecorp.icims.com",
        },
    )


def test_normalize_populates_basic_fields() -> None:
    raw = _make_raw("acmecorp_detail_12345.html", "12345", "senior-product-manager-platform")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.raw_title == "Senior Product Manager, Platform"
    assert norm.location_raw == "New York, NY, US"
    assert norm.canonical_company_name == "AcmeCorp"
    assert norm.ats == "icims"
    assert norm.source_job_id == "12345"
    assert norm.parser_version == "icims-v1"


def test_normalize_strips_html_from_jd() -> None:
    raw = _make_raw("acmecorp_detail_12345.html", "12345", "slug")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert "<p>" not in norm.jd_text
    assert "developer-platform roadmap" in norm.jd_text


def test_normalize_extracts_salary_into_min_max_currency() -> None:
    raw = _make_raw("acmecorp_detail_12345.html", "12345", "slug")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.salary_min == 180_000
    assert norm.salary_max == 240_000
    assert norm.salary_currency == "USD"
    assert norm.salary_period == "annual"


def test_normalize_leaves_salary_null_when_missing() -> None:
    raw = _make_raw("acmecorp_detail_12346.html", "12346", "slug")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.salary_min is None
    assert norm.salary_max is None
    assert norm.salary_currency is None
    assert norm.salary_period == "unknown"


def test_normalize_remote_type_from_telecommute_jsonld() -> None:
    raw = _make_raw("acmecorp_detail_12345.html", "12345", "slug")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.remote_type == "remote"


def test_normalize_remote_type_hybrid_from_jsonld() -> None:
    raw = _make_raw("acmecorp_detail_12346.html", "12346", "slug")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.remote_type == "hybrid"


def test_normalize_extracts_department_from_industry() -> None:
    raw = _make_raw("acmecorp_detail_12345.html", "12345", "slug")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.department == "Product"
    assert norm.team is None


def test_normalize_uses_jsonld_url_when_present() -> None:
    raw = _make_raw("acmecorp_detail_12345.html", "12345", "senior-product-manager-platform")
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.source_url == (
        "https://careers-acmecorp.icims.com/jobs/12345/senior-product-manager-platform/job"
    )


def test_normalize_falls_back_to_listing_row_url() -> None:
    """When JSON-LD omits ``url``, the constructed careers_url +
    listing_row id/slug must produce a valid detail URL."""
    raw = RawPosting(
        source_job_id="99999",
        raw_payload={
            "listing_row": {
                "source_job_id": "99999",
                "slug": "some-role",
                "raw_title": "Some Role",
            },
            "detail_html": "",
            "jsonld": {"@type": "JobPosting", "title": "Some Role"},
            "careers_url": "https://careers-acmecorp.icims.com",
        },
    )
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.source_url == "https://careers-acmecorp.icims.com/jobs/99999/some-role/job"


def test_normalize_handles_empty_payload_gracefully() -> None:
    """Adapter must not crash on a minimal payload — missing fields fall
    through to defaults / None."""
    raw = RawPosting(source_job_id="0", raw_payload={})
    norm = _make_adapter().normalize(raw, "AcmeCorp")
    assert norm.ats == "icims"
    assert norm.source_job_id == "0"
    assert norm.canonical_company_name == "AcmeCorp"


# ── fetch_postings via MockTransport ────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_postings_walks_listing_and_details() -> None:
    """Listing returns 3 jobs; adapter then fetches 3 detail pages and
    returns 3 RawPosting rows with the merged payload shape."""
    listing = _load("acmecorp_listing.html")
    detail_12345 = _load("acmecorp_detail_12345.html")
    detail_12346 = _load("acmecorp_detail_12346.html")
    detail_12347 = _load("acmecorp_detail_12347.html")

    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "/jobs/search?" in url and "offset=0" in url:
            return httpx.Response(200, text=listing)
        if "/jobs/search?" in url:
            # Subsequent offset requests are empty — stops pagination.
            return httpx.Response(200, text="<html><body></body></html>")
        if "/jobs/12345/" in url:
            return httpx.Response(200, text=detail_12345)
        if "/jobs/12346/" in url:
            return httpx.Response(200, text=detail_12346)
        if "/jobs/12347/" in url:
            return httpx.Response(200, text=detail_12347)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with ICIMSAdapter(client=client) as adapter:
        postings = await adapter.fetch_postings("acmecorp")
    await client.aclose()

    ids = sorted(p.source_job_id for p in postings)
    assert ids == ["12345", "12346", "12347"]
    # Each posting carries the merged payload shape.
    p1 = next(p for p in postings if p.source_job_id == "12345")
    assert p1.raw_payload["jsonld"]["title"] == "Senior Product Manager, Platform"
    assert p1.raw_payload["careers_url"] == "https://careers-acmecorp.icims.com"


@pytest.mark.asyncio
async def test_fetch_postings_respects_careers_url_override() -> None:
    """``adapter_config.careers_url`` swaps the default subdomain shape."""
    captured: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, text="<html><body></body></html>")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with ICIMSAdapter(
        adapter_config={"careers_url": "https://jobs.example.com"},
        client=client,
    ) as adapter:
        await adapter.fetch_postings("acmecorp")
    await client.aclose()

    assert captured[0].startswith("https://jobs.example.com/jobs/search?")


@pytest.mark.asyncio
async def test_fetch_postings_handles_429_with_retry() -> None:
    """First 429 retried via tenacity. Mirrors Workday adapter contract."""
    listing = _load("acmecorp_listing.html")
    detail = _load("acmecorp_detail_12345.html")
    attempts = {"listing": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/jobs/search?" in url:
            attempts["listing"] += 1
            if attempts["listing"] == 1:
                return httpx.Response(429)
            if attempts["listing"] == 2:
                return httpx.Response(200, text=listing)
            return httpx.Response(200, text="<html><body></body></html>")
        return httpx.Response(200, text=detail)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with ICIMSAdapter(client=client) as adapter:
        postings = await adapter.fetch_postings("acmecorp")
    await client.aclose()

    assert attempts["listing"] >= 2
    assert len(postings) >= 1


@pytest.mark.asyncio
async def test_fetch_postings_returns_empty_when_listing_5xx_persists() -> None:
    """Persistent 503 → tenacity exhausts retries → returns []. The run
    surface (IngestRun.status='failed') is the operator-facing signal,
    not a per-source row."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with ICIMSAdapter(client=client) as adapter:
        postings = await adapter.fetch_postings("acmecorp")
    await client.aclose()

    assert postings == []


@pytest.mark.asyncio
async def test_fetch_postings_stops_paginating_on_repeat_ids() -> None:
    """Tenants that ignore the ``offset`` param return the same listing
    on every request. The adapter must detect that (all rows already
    seen) and stop, not loop forever."""
    listing = _load("acmecorp_listing.html")
    detail = _load("acmecorp_detail_12345.html")

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/jobs/search?" in url:
            return httpx.Response(200, text=listing)  # same body always
        return httpx.Response(200, text=detail)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    async with ICIMSAdapter(client=client) as adapter:
        postings = await adapter.fetch_postings("acmecorp")
    await client.aclose()

    # Exactly 3 unique postings, not 3 * _MAX_PAGES.
    assert len(postings) == 3


# ── Surface contract — locks adapter against silent breakage ────────────


def test_adapter_class_attributes_match_protocol() -> None:
    """``ats`` + ``parser_version`` ClassVars must be present and stable.
    Other code (IngestionService, ingest_run.source) reads these."""
    assert ICIMSAdapter.ats == "icims"
    assert ICIMSAdapter.parser_version == "icims-v1"


def test_ats_enum_contains_icims() -> None:
    """Locks the migration + ATS StrEnum extension together."""
    from job_assist.db.enums import ATS

    assert ATS.icims == "icims"


def test_main_dispatch_constants_include_icims() -> None:
    """Three dispatch sites must all include 'icims' — the TODO(adapter-
    dispatch-drift) tag in main.py warns about this fragility."""
    from job_assist.main import _INGESTABLE_ATS

    assert "icims" in _INGESTABLE_ATS


def test_cli_supported_set_includes_icims() -> None:
    from job_assist.cli import _SUPPORTED_ATS

    assert "icims" in _SUPPORTED_ATS


# ── JSON-LD field-shape contract — pinned by the fixture set ────────────


def test_fixture_jobposting_required_fields_present() -> None:
    """If a fixture's JSON-LD ever loses required keys, this test
    catches it before the parser silently produces empty rows."""
    for filename in (
        "acmecorp_detail_12345.html",
        "acmecorp_detail_12346.html",
        "acmecorp_detail_12347.html",
    ):
        jsonld = _extract_jsonld(_load(filename))
        assert jsonld is not None, f"{filename} JSON-LD missing"
        assert jsonld.get("@type") == "JobPosting"
        assert isinstance(jsonld.get("title"), str)
        # description is HTML-string; some real iCIMS pages emit empty
        # description on inactive postings — fixture mirrors the dominant
        # populated case.
        assert isinstance(jsonld.get("description"), str)


def test_listing_fixture_yields_three_unique_ids() -> None:
    """Locks the fixture corpus size — if someone trims or adds a row,
    other parser tests break in a comprehensible way."""
    rows = _extract_listing_rows(_load("acmecorp_listing.html"))
    assert len(rows) == 3


def test_workday_test_pattern_still_independent() -> None:
    """Sanity: the iCIMS test module imports nothing from the Workday
    adapter or its tests. Locks the no-base-class-refactor strip."""
    # If this assertion needs to change in a future PR, it means we're
    # genuinely sharing code between adapters and the time to extract a
    # base class has come. Until then, copy-paste over coupling.
    icims_module = __import__("job_assist.adapters.icims", fromlist=["ICIMSAdapter"])
    workday_imports = [
        name for name in dir(icims_module) if "workday" in name.lower() or "Workday" in name
    ]
    assert workday_imports == []


# Belt-and-suspenders: locks the JSON shape we expect on baseSalary so a
# future fixture rewrite can't silently drop the integration.
def test_basesalary_shape_pinned_to_documented_form() -> None:
    jsonld = _extract_jsonld(_load("acmecorp_detail_12345.html"))
    assert jsonld is not None
    base = jsonld["baseSalary"]
    assert base["currency"] == "USD"
    value = base["value"]
    assert value["@type"] == "QuantitativeValue"
    assert value["minValue"] == 180_000
    assert value["maxValue"] == 240_000
