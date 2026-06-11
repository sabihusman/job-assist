"""Fantastic.jobs (Apify) adapter tests — feat/fantastic-jobs-ingest.

The mapper / input-builder / filter tests are PURE (no network, no httpx
client) so they run anywhere. The fetch_postings test injects an httpx
MockTransport — it runs on CI (this Windows dev box can't init httpx's SSL:
OpenSSL applink).
"""

from __future__ import annotations

import httpx
import pytest

from job_assist.adapters.fantastic_jobs import (
    TITLE_EXCLUSION_SEARCH,
    TITLE_SEARCH,
    FantasticJobsAdapter,
    _map_geo,
    _parse_date,
    _source_job_id,
    build_actor_input,
    map_record,
)

# A representative Apify record — note the malformed ``organization_url``.
_REC = {
    "id": "job-123",
    "title": "Product Manager, New Markets",
    "organization": "Athene",
    "cities_derived": ["West Des Moines"],
    "regions_derived": ["Iowa"],
    "countries_derived": ["United States"],
    "salary_raw": "$110,000 - $140,000 a year",
    "url": "https://athene.wd5.myworkdayjobs.com/athene_careers/job/R-123",
    "description_text": "We are hiring a Product Manager for New Markets.",
    "date_posted": "2026-06-01T00:00:00Z",
    "employment_type": ["FULL_TIME"],
    "organization_url": "Failed to construct 'URL': Invalid URL",
}


# ── input builder + filter ───────────────────────────────────────────────────


def test_build_actor_input_prefers_domain() -> None:
    body = build_actor_input(organization="Athene", domain="athene.com", limit=50)
    assert body["domainFilter"] == ["athene.com"]
    assert "organizationSearch" not in body
    assert body["titleSearch"] == TITLE_SEARCH
    assert body["titleExclusionSearch"] == TITLE_EXCLUSION_SEARCH
    assert body["limit"] == 50


def test_build_actor_input_falls_back_to_org_name() -> None:
    body = build_actor_input(organization="EMC Insurance", domain=None)
    assert body["organizationSearch"] == ["EMC Insurance"]
    assert "domainFilter" not in body


def test_build_actor_input_unfiltered_omits_title_filter() -> None:
    # The diagnostic probe: domain targeting kept, PM/PO title filter dropped.
    body = build_actor_input(organization="Athene", domain="athene.com", include_title_filter=False)
    assert body["domainFilter"] == ["athene.com"]
    assert "titleSearch" not in body
    assert "titleExclusionSearch" not in body


def test_title_filter_asymmetry_protects_senior_product_owner() -> None:
    # Include captures all five wanted titles (base/associate PM/PO + senior PO).
    assert TITLE_SEARCH == ["Product Manager", "Product Owner"]
    # Senior-PM is excluded as a MULTI-WORD term...
    assert "Senior Product Manager" in TITLE_EXCLUSION_SEARCH
    # ...but bare "Senior" is NOT excluded — that would kill Senior Product Owner.
    assert "Senior" not in TITLE_EXCLUSION_SEARCH
    assert not any(t.strip().lower() == "senior" for t in TITLE_EXCLUSION_SEARCH)
    # Wrong role types are excluded.
    for t in ("Project Manager", "Program Manager", "Product Marketing"):
        assert t in TITLE_EXCLUSION_SEARCH


# ── mapper ───────────────────────────────────────────────────────────────────


def test_map_record_ignores_organization_url_and_uses_clean_url() -> None:
    np = map_record(_REC, "Athene", ats="workday", source_job_id="job-123")
    # The clean apply link is ``url`` — never organization_url.
    assert np.source_url == _REC["url"]
    assert np.apply_url == _REC["url"]
    # The poison field is stripped from the stored payload entirely.
    assert "organization_url" not in np.raw_payload
    assert "Failed to construct" not in str(np.raw_payload)


def test_map_record_maps_core_fields() -> None:
    np = map_record(_REC, "Athene", ats="workday", source_job_id="job-123")
    assert np.canonical_company_name == "Athene"
    assert np.raw_title == "Product Manager, New Markets"
    assert np.ats == "workday"
    assert np.parser_version == "fantastic-v1"
    assert np.content_hash  # non-empty
    assert np.source_job_id == "job-123"
    # Derived geo → city/state shape.
    assert np.locations_normalized == [
        {
            "city": "West Des Moines",
            "state": "Iowa",
            "country": "United States",
            "remote_type": "onsite",
        }
    ]
    assert np.location_raw == "West Des Moines, Iowa"
    assert np.remote_type == "onsite"
    # date_posted parsed to an aware datetime.
    assert np.posted_at is not None and np.posted_at.tzinfo is not None


def test_map_geo_remote() -> None:
    entries, _raw, remote = _map_geo({"cities_derived": ["Remote"], "regions_derived": []})
    assert {"remote_type": "remote"} in entries
    assert remote == "remote"


def test_map_geo_empty() -> None:
    entries, raw_loc, remote = _map_geo({})
    assert entries == []
    assert raw_loc is None
    assert remote == "unknown"


def test_source_job_id_prefers_id_then_hashes_url() -> None:
    assert _source_job_id({"id": "abc"}) == "abc"
    h = _source_job_id({"url": "https://x.test/job/1"})
    assert h and h != "https://x.test/job/1"  # hashed, not the raw url


def test_parse_date_variants() -> None:
    assert _parse_date("2026-06-01T00:00:00Z") is not None
    assert _parse_date("2026-06-01") is not None
    assert _parse_date("not a date") is None
    assert _parse_date(None) is None


# ── fetch (CI — needs httpx) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_postings_calls_apify_and_wraps_records() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[_REC, {"id": "job-456", "title": "Product Owner"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FantasticJobsAdapter(
        organization="Athene", domain="athene.com", ats="workday", token="tok-xyz", client=client
    )
    async with adapter:
        raws = await adapter.fetch_postings("athene")

    assert len(raws) == 2
    assert raws[0].source_job_id == "job-123"
    assert "run-sync-get-dataset-items" in str(captured["url"])
    assert captured["auth"] == "Bearer tok-xyz"
    assert captured["body"]["domainFilter"] == ["athene.com"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_fetch_postings_without_token_raises() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    )
    adapter = FantasticJobsAdapter(
        organization="Athene", domain=None, ats="workday", token="", client=client
    )
    async with adapter:
        with pytest.raises(RuntimeError, match="APIFY_API_TOKEN"):
            await adapter.fetch_postings("athene")


# ── feat/strategy-spine: per-track actor input ────────────────────────────────


def test_build_actor_input_strategy_track_widens_search() -> None:
    body = build_actor_input(organization="John Deere", domain="deere.com", track="strategy")
    # PM/PO terms still present; strategy family added.
    for term in ["Product Manager", "Product Owner", "Corporate Strategy", "Chief of Staff"]:
        assert term in body["titleSearch"]
    # The strategy-safe exclusion list — none of the bare seniority tokens
    # that would tsquery-kill "Chief of Staff" / "Strategy Lead".
    for tok in ["Chief", "Staff", "Lead", "Director", "Head", "VP", "Principal", "Group"]:
        assert tok not in body["titleExclusionSearch"]
    # PM seniority + wrong-family exclusions survive.
    assert "Senior Product Manager" in body["titleExclusionSearch"]
    assert "Program Manager" in body["titleExclusionSearch"]


def test_build_actor_input_default_track_unchanged() -> None:
    """The curated (pm) track is byte-identical to the pre-strategy filter."""
    body = build_actor_input(organization="Athene", domain="athene.com")
    assert body["titleSearch"] == ["Product Manager", "Product Owner"]
    assert "Chief" in body["titleExclusionSearch"]  # the locked PM band exclusions
    assert "Corporate Strategy" not in body["titleSearch"]
