"""Pure tests for adapter ``peek_title`` (feat/ingest-title-prefilter).

Each adapter's ``peek_title`` MUST extract the same string the rest of
the pipeline would derive from ``raw.raw_payload``. The contract is
narrow but load-bearing: the title pre-filter sees the same value the
normalizer would later set as ``raw_title``, so any drift between the
two would cause the pre-filter to drop postings whose normalized title
would have been a PM-cluster keep (or vice versa).

These are pure tests (no DB, no HTTP) — they instantiate the adapter,
build a minimal ``RawPosting`` for the per-adapter payload shape, and
assert ``peek_title()`` returns the expected string. Adapters' real
HTTP / pagination behaviour is covered by the existing per-adapter
suites; this file ONLY pins the cheap title extraction.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import httpx

from job_assist.adapters.ashby import AshbyAdapter
from job_assist.adapters.base import RawPosting
from job_assist.adapters.greenhouse import GreenhouseAdapter
from job_assist.adapters.icims import ICIMSAdapter
from job_assist.adapters.lever import LeverAdapter
from job_assist.adapters.workday import WorkdayAdapter


def _raw(payload: dict[str, Any]) -> RawPosting:
    """Build the minimal RawPosting all adapters need."""
    return RawPosting(source_job_id="x", raw_payload=payload)


def _mock_client() -> httpx.AsyncClient:
    """Mock httpx client — ``peek_title`` is a pure-Python method that
    never touches the network, so a stub satisfies the adapter
    constructor without paying the real ``httpx.AsyncClient`` init
    cost (which on some Windows shells deadlocks on an OpenSSL DLL
    init conflict). Cast for typecheck only — no method on the mock
    is ever called by the tests below."""
    return cast(httpx.AsyncClient, MagicMock(spec=httpx.AsyncClient))


# ── Greenhouse: title lives under ``title`` ─────────────────────────────────


def test_greenhouse_peek_title_extracts_title_key() -> None:
    assert GreenhouseAdapter(client=_mock_client()).peek_title(_raw({"title": "Senior Product Manager"})) == (
        "Senior Product Manager"
    )


def test_greenhouse_peek_title_missing_returns_empty() -> None:
    assert GreenhouseAdapter(client=_mock_client()).peek_title(_raw({})) == ""


# ── Lever: title lives under ``text``, NOT ``title`` ────────────────────────


def test_lever_peek_title_extracts_text_key() -> None:
    """The load-bearing one — Lever's title key is ``text``, not
    ``title``. A naive default implementation would silently miss
    every Lever posting."""
    assert LeverAdapter(client=_mock_client()).peek_title(_raw({"text": "Group Product Manager"})) == (
        "Group Product Manager"
    )


def test_lever_peek_title_ignores_title_key_uses_text() -> None:
    """If Lever ever starts populating both, ``text`` still wins —
    matching ``normalize()``'s extraction."""
    result = LeverAdapter(client=_mock_client()).peek_title(_raw({"text": "Senior PM", "title": "Wrong"}))
    assert result == "Senior PM"


# ── Ashby: title lives under ``title`` ──────────────────────────────────────


def test_ashby_peek_title_extracts_title_key() -> None:
    assert AshbyAdapter(client=_mock_client()).peek_title(_raw({"title": "Staff Product Manager"})) == (
        "Staff Product Manager"
    )


# ── Workday: merged ``{list, detail}`` payload — detail wins ───────────────


def test_workday_peek_title_prefers_detail_jobPostingInfo_title() -> None:
    """Detail's ``jobPostingInfo.title`` is authoritative when both
    sources are present (mirrors ``normalize()``)."""
    payload = {
        "list": {"title": "Stale List Title"},
        "detail": {"jobPostingInfo": {"title": "Senior Product Manager"}},
    }
    assert WorkdayAdapter(client=_mock_client()).peek_title(_raw(payload)) == "Senior Product Manager"


def test_workday_peek_title_falls_back_to_list_title() -> None:
    """When the detail payload's jobPostingInfo is missing, fall
    through to the list-level title."""
    payload = {"list": {"title": "Product Manager"}, "detail": {}}
    assert WorkdayAdapter(client=_mock_client()).peek_title(_raw(payload)) == "Product Manager"


def test_workday_peek_title_non_dict_payload_returns_empty() -> None:
    """Defensive — a malformed RawPosting where raw_payload isn't a
    dict (shouldn't happen but the model permits anything) returns
    empty rather than raising."""
    raw = RawPosting(source_job_id="x", raw_payload={"list": None, "detail": None})  # type: ignore[arg-type]
    assert WorkdayAdapter(client=_mock_client()).peek_title(raw) == ""


# ── iCIMS: merged JSON-LD + listing row, JSON-LD wins ──────────────────────


def test_icims_peek_title_prefers_jsonld_title() -> None:
    """JSON-LD is the authoritative source per ``normalize()``."""
    payload = {
        "jsonld": {"title": "Principal Product Manager"},
        "listing_row": {"raw_title": "Stale Listing Title"},
    }
    assert ICIMSAdapter(client=_mock_client()).peek_title(_raw(payload)) == "Principal Product Manager"


def test_icims_peek_title_falls_back_to_listing_raw_title() -> None:
    """When JSON-LD is empty (detail page didn't load yet, or the
    detail-fetch failed silently), use the listing row's ``raw_title``."""
    payload = {
        "jsonld": {},
        "listing_row": {"raw_title": "Product Manager"},
    }
    assert ICIMSAdapter(client=_mock_client()).peek_title(_raw(payload)) == "Product Manager"


def test_icims_peek_title_empty_payload_returns_empty() -> None:
    assert ICIMSAdapter(client=_mock_client()).peek_title(_raw({})) == ""
