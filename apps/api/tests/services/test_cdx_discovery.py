"""Unit tests for services/cdx_discovery.py (Slice 3b).

Pure — no network, no DB. Pins the slug-extraction regex, host→ats
mapping, reserved-segment exclusions, CDX status filter, JSONL parsing,
and dedup. The network orchestration in scripts/discover_handles.py is
not unit-tested (live Common Crawl calls); these helpers are the part
that must be correct.
"""

from __future__ import annotations

import pytest

from job_assist.services.cdx_discovery import (
    dedup_against_existing,
    extract_slug,
    host_to_ats,
    parse_cdx_jsonl,
    slugs_from_cdx_records,
)

# ── host_to_ats ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("boards.greenhouse.io", "greenhouse"),
        ("job-boards.greenhouse.io", "greenhouse"),
        ("jobs.lever.co", "lever"),
        ("jobs.ashbyhq.com", "ashby"),
        ("www.jobs.lever.co", "lever"),  # www stripped
        ("jobs.lever.co:443", "lever"),  # port stripped
        ("BOARDS.GREENHOUSE.IO", "greenhouse"),  # case-insensitive
        ("example.com", None),
        ("api.greenhouse.io", None),  # not a board host
    ],
)
def test_host_to_ats(host: str, expected: str | None) -> None:
    assert host_to_ats(host) == expected


# ── extract_slug ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
        ("https://boards.greenhouse.io/stripe/jobs/12345", ("greenhouse", "stripe")),
        ("https://job-boards.greenhouse.io/gusto/jobs/9", ("greenhouse", "gusto")),
        ("https://jobs.lever.co/alpaca", ("lever", "alpaca")),
        ("https://jobs.lever.co/alpaca/abc-def-123", ("lever", "alpaca")),
        ("https://jobs.ashbyhq.com/modern-treasury", ("ashby", "modern-treasury")),
        ("https://jobs.ashbyhq.com/ramp/uuid-here/application", ("ashby", "ramp")),
        # Slug normalized to lowercase.
        ("https://boards.greenhouse.io/Stripe", ("greenhouse", "stripe")),
        # Scheme-less input is tolerated.
        ("boards.greenhouse.io/notion", ("greenhouse", "notion")),
    ],
)
def test_extract_slug_valid(url: str, expected: tuple[str, str]) -> None:
    assert extract_slug(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/",  # bare root, no slug
        "https://boards.greenhouse.io/embed/job_app?token=1",  # reserved
        "https://boards.greenhouse.io/favicon.ico",  # reserved
        "https://boards.greenhouse.io/static/main.css",  # reserved
        "https://jobs.lever.co/api/postings",  # reserved 'api'
        "https://example.com/stripe",  # not an ATS host
        "https://boards.greenhouse.io/%20broken",  # junk slug (regex reject)
        "https://boards.greenhouse.io/Foo.Bar",  # dot → regex reject
    ],
)
def test_extract_slug_rejected(url: str) -> None:
    assert extract_slug(url) is None


def test_extract_slug_expected_ats_mismatch_returns_none() -> None:
    """When pinning one host's ATS, a URL from a different ATS is dropped."""
    assert extract_slug("https://jobs.lever.co/alpaca", expected_ats="greenhouse") is None
    assert extract_slug("https://jobs.lever.co/alpaca", expected_ats="lever") == ("lever", "alpaca")


# ── parse_cdx_jsonl ─────────────────────────────────────────────────────────


def test_parse_cdx_jsonl_skips_blank_and_bad_lines() -> None:
    text = (
        '{"url": "https://jobs.lever.co/alpaca", "status": "200"}\n'
        "\n"  # blank
        "not json at all\n"  # garbage
        '{"no_url_field": true}\n'  # missing url → skipped
        '{"url": "https://jobs.lever.co/brex", "status": "301"}\n'
    )
    records = list(parse_cdx_jsonl(text))
    assert len(records) == 2
    assert records[0]["url"].endswith("/alpaca")
    assert records[1]["url"].endswith("/brex")


# ── slugs_from_cdx_records ──────────────────────────────────────────────────


def test_slugs_from_cdx_records_status_filter_and_dedup() -> None:
    records = [
        {"url": "https://boards.greenhouse.io/stripe", "status": "200"},
        {"url": "https://boards.greenhouse.io/stripe/jobs/1", "status": "200"},  # dup slug
        {"url": "https://boards.greenhouse.io/gusto", "status": "301"},  # redirect ok
        {"url": "https://boards.greenhouse.io/deadco", "status": "404"},  # dropped
        {"url": "https://boards.greenhouse.io/errco", "status": "503"},  # dropped
        {"url": "https://boards.greenhouse.io/embed", "status": "200"},  # reserved
    ]
    slugs = slugs_from_cdx_records(records, expected_ats="greenhouse")
    assert slugs == {"stripe", "gusto"}


def test_slugs_from_cdx_records_ignores_other_ats_hosts() -> None:
    """A Lever URL accidentally in a greenhouse-host result set is dropped."""
    records = [
        {"url": "https://boards.greenhouse.io/stripe", "status": "200"},
        {"url": "https://jobs.lever.co/alpaca", "status": "200"},
    ]
    assert slugs_from_cdx_records(records, expected_ats="greenhouse") == {"stripe"}


# ── dedup_against_existing ──────────────────────────────────────────────────


def test_dedup_against_existing_drops_known_sorts_output() -> None:
    candidates = {
        "greenhouse": {"stripe", "gusto", "altruist"},
        "lever": {"alpaca"},
    }
    existing = {("greenhouse", "stripe"), ("lever", "alpaca")}
    new = dedup_against_existing(candidates, existing)
    # Sorted by ats then slug; known pairs removed.
    assert new == [("greenhouse", "altruist"), ("greenhouse", "gusto")]


def test_dedup_against_existing_empty_existing_returns_all_sorted() -> None:
    candidates = {"ashby": {"ramp", "modern-treasury"}}
    assert dedup_against_existing(candidates, set()) == [
        ("ashby", "modern-treasury"),
        ("ashby", "ramp"),
    ]
