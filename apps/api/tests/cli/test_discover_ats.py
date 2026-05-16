"""Unit tests for discover-ats handle generation and ATS probing logic."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from job_assist.cli import _probe_company, candidate_handles

# ── Handle generation ──────────────────────────────────────────────────────────


class TestCandidateHandles:
    @pytest.mark.parametrize(
        "name, must_contain",
        [
            ("Stripe", ["stripe"]),
            ("Capital One", ["capitalone", "capital-one", "capital"]),
            ("Q2 Holdings", ["q2holdings", "q2-holdings", "q2"]),
            ("Anthropic", ["anthropic"]),
            (
                "Morgan Stanley Wealth Management",
                ["morganstanleywealthmanagement", "morganstanley"],
            ),
            # Synthetic five-word name with the same shape as real long FS-incumbent
            # names — exercises the "all words joined" branch for long handles.
            ("Acme Insurance Cross Shield Group", ["acmeinsurancecro"]),  # prefix check
        ],
    )
    def test_contains_expected_handles(self, name: str, must_contain: list[str]) -> None:
        handles = candidate_handles(name)
        assert len(handles) > 0, f"No handles generated for {name!r}"
        for expected in must_contain:
            assert any(h.startswith(expected) or h == expected for h in handles), (
                f"Expected handle starting with {expected!r} not in {handles} for {name!r}"
            )

    def test_no_duplicates(self) -> None:
        for name in ("Stripe", "Capital One", "Morgan Stanley Wealth Management"):
            handles = candidate_handles(name)
            assert len(handles) == len(set(handles)), f"Duplicate handles for {name!r}"

    def test_all_lowercase(self) -> None:
        handles = candidate_handles("Capital One")
        for h in handles:
            assert h == h.lower(), f"Handle not lowercase: {h!r}"


# ── ATS probing ────────────────────────────────────────────────────────────────


def _mock_client(responses: dict[str, tuple[int, Any]]) -> AsyncMock:
    """Build an AsyncMock httpx.AsyncClient with canned URL → (status, json) responses."""

    async def mock_get(url: str, **kwargs: Any) -> MagicMock:
        status, body = responses.get(url, (404, {}))
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status
        resp.json.return_value = body
        return resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=mock_get)
    return client


class TestProbeCompany:
    async def test_stripe_resolves_greenhouse(self) -> None:
        """Stripe should resolve to Greenhouse using the 'stripe' handle."""
        greenhouse_url = "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
        jobs_payload = {"jobs": [{"id": 1}, {"id": 2}]}
        client = _mock_client({greenhouse_url: (200, jobs_payload)})

        result = await _probe_company("Stripe", client)

        assert result is not None
        assert result["ats"] == "greenhouse"
        assert result["handle"] == "stripe"
        assert result["job_count"] == 2

    async def test_unknown_company_returns_none(self) -> None:
        """A company with no matching ATS should return None."""
        # All probes return 404
        client = _mock_client({})
        result = await _probe_company("NonExistentCompanyXYZ999", client)
        assert result is None

    async def test_network_error_does_not_raise(self) -> None:
        """Network errors per-URL must be swallowed; result is None."""

        async def failing_get(url: str, **kwargs: Any) -> None:
            raise httpx.TimeoutException("timeout", request=MagicMock())

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=failing_get)

        # Should not raise — returns None gracefully
        result = await _probe_company("SomeCompany", client)
        assert result is None

    async def test_lever_response_detected(self) -> None:
        """A valid Lever response (list) should be detected correctly."""
        lever_url = "https://api.lever.co/v0/postings/acmecorp?mode=json"
        client = _mock_client({lever_url: (200, [{"id": "abc"}, {"id": "def"}])})

        result = await _probe_company("Acmecorp", client)

        assert result is not None
        assert result["ats"] == "lever"
        assert result["handle"] == "acmecorp"
        assert result["job_count"] == 2


class TestFirstWordFallbackThreshold:
    """Multi-word company names must clear a posting-count threshold when the
    match comes solely via the first-word fallback (e.g. 'charles' for
    'Charles Schwab'). Single-word names are unaffected.
    """

    async def test_low_count_first_word_match_rejected(self) -> None:
        """'Charles Schwab' must NOT match greenhouse/charles with only 3 postings."""
        # The full-name and hyphenated candidates must 404 so the loop reaches
        # the first-word fallback. Only that one returns a (small) job list.
        client = _mock_client(
            {
                "https://boards-api.greenhouse.io/v1/boards/charles/jobs": (
                    200,
                    {"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]},
                ),
            }
        )
        result = await _probe_company("Charles Schwab", client)
        assert result is None, "3 postings is below the fallback threshold; expected no match"

    async def test_high_count_first_word_match_accepted(self) -> None:
        """A first-word match still accepts when the board has substantive postings."""
        client = _mock_client(
            {
                "https://boards-api.greenhouse.io/v1/boards/charles/jobs": (
                    200,
                    {"jobs": [{"id": i} for i in range(10)]},
                ),
            }
        )
        result = await _probe_company("Charles Schwab", client)
        assert result is not None
        assert result["handle"] == "charles"
        assert result["job_count"] == 10

    async def test_single_word_name_not_affected(self) -> None:
        """Single-word names like 'Stripe' match at any count — rule doesn't apply."""
        client = _mock_client(
            {
                "https://boards-api.greenhouse.io/v1/boards/stripe/jobs": (
                    200,
                    {"jobs": [{"id": 1}]},
                ),
            }
        )
        result = await _probe_company("Stripe", client)
        assert result is not None
        assert result["handle"] == "stripe"
        assert result["job_count"] == 1

    async def test_multiword_full_name_match_not_gated(self) -> None:
        """A multi-word match via the full hyphenated handle bypasses the threshold."""
        # 'charles-schwab' is the hyphenated full-name candidate — not a first-word
        # fallback — so even 1 posting should be accepted.
        client = _mock_client(
            {
                "https://boards-api.greenhouse.io/v1/boards/charles-schwab/jobs": (
                    200,
                    {"jobs": [{"id": 1}]},
                ),
            }
        )
        result = await _probe_company("Charles Schwab", client)
        assert result is not None
        assert result["handle"] == "charles-schwab"
        assert result["job_count"] == 1
