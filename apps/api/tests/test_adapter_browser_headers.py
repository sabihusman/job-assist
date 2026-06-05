"""The Workday + iCIMS adapters must send browser-like headers
(fix/datacenter-egress-headers).

Both boards return empty/challenge responses to the default ``python-httpx``
User-Agent from datacenter IPs (Railway), which surfaced as
``postings_fetched=0, status=success`` while the same boards returned full
results to a residential IP. These pin that the default client carries a
realistic browser UA + Accept-Language so a UA-based block is cleared.

Pure construction tests — no DB, no network — so they run anywhere.
"""

from __future__ import annotations

import pytest

from job_assist.adapters.base import BROWSER_HEADERS
from job_assist.adapters.icims import ICIMSAdapter
from job_assist.adapters.workday import WorkdayAdapter


def test_browser_headers_look_like_a_real_browser() -> None:
    assert "Mozilla/5.0" in BROWSER_HEADERS["User-Agent"]
    assert "Chrome/" in BROWSER_HEADERS["User-Agent"]
    assert "python-httpx" not in BROWSER_HEADERS["User-Agent"]
    assert BROWSER_HEADERS["Accept-Language"].startswith("en")


@pytest.mark.asyncio
async def test_workday_default_client_sends_browser_ua() -> None:
    async with WorkdayAdapter(adapter_config={"wd_number": "wd5", "site": "x"}) as a:
        # httpx lowercases header names.
        assert a._client.headers["user-agent"] == BROWSER_HEADERS["User-Agent"]
        assert a._client.headers["accept-language"] == BROWSER_HEADERS["Accept-Language"]


@pytest.mark.asyncio
async def test_icims_default_client_sends_browser_ua() -> None:
    async with ICIMSAdapter(adapter_config={"careers_url": "https://jobs.example.com"}) as a:
        assert a._client.headers["user-agent"] == BROWSER_HEADERS["User-Agent"]
        assert a._client.headers["accept-language"] == BROWSER_HEADERS["Accept-Language"]


@pytest.mark.asyncio
async def test_injected_client_is_left_untouched() -> None:
    """When a caller injects its own client (the test seam), the adapter must
    not clobber it — it only sets headers on the default client it creates."""
    import httpx

    custom = httpx.AsyncClient()
    try:
        a = WorkdayAdapter(adapter_config={"wd_number": "wd5", "site": "x"}, client=custom)
        assert a._client is custom
        assert "user-agent" not in {k.lower() for k in custom.headers} or "python-httpx" in (
            custom.headers.get("user-agent", "")
        )
    finally:
        await custom.aclose()
