"""Unit tests for shared adapter normalization helpers.

The seniority / role-family / location / HTML helpers are already
exercised via the Greenhouse and Lever adapter test suites. This file
focuses on parse_compensation, which is new in PR #4 and will be reused
by Workday + JSearch adapters later.
"""

from __future__ import annotations

import pytest

from job_assist.adapters.normalization import parse_compensation


class TestParseCompensation:
    @pytest.mark.parametrize(
        "summary, expected",
        [
            # ── Single value, K suffix ────────────────────────────────────
            ("$150K", (150_000, 150_000, "USD", "annual")),
            ("$ 150K", (150_000, 150_000, "USD", "annual")),
            # ── Annual range, K suffix ────────────────────────────────────
            ("$140K – $180K", (140_000, 180_000, "USD", "annual")),
            ("$140K - $180K", (140_000, 180_000, "USD", "annual")),
            # ── Annual range, explicit thousands separators ───────────────
            ("$140,000 – $180,000", (140_000, 180_000, "USD", "annual")),
            ("$140,000 to $180,000", (140_000, 180_000, "USD", "annual")),
            # ── Hourly ───────────────────────────────────────────────────
            ("$50/hr – $75/hr", (50, 75, "USD", "hourly")),
            ("$50 / hour", (50, 50, "USD", "hourly")),
            # ── Non-USD currencies ───────────────────────────────────────
            ("£100K", (100_000, 100_000, "GBP", "annual")),
            ("£90K – £120K", (90_000, 120_000, "GBP", "annual")),
            ("€100K", (100_000, 100_000, "EUR", "annual")),
            ("C$120K – C$150K", (120_000, 150_000, "CAD", "annual")),
            # ── Empty / None / unparseable ───────────────────────────────
            (None, (None, None, None, None)),
            ("", (None, None, None, None)),
            ("   ", (None, None, None, None)),
            ("Competitive — DOE", (None, None, None, None)),
            ("Salary range not disclosed", (None, None, None, None)),
            # ── Stray numbers without currency anchor are ignored ────────
            ("Q3 2026 hire, $150K base", (150_000, 150_000, "USD", "annual")),
        ],
    )
    def test_parse(
        self,
        summary: str | None,
        expected: tuple[int | None, int | None, str | None, str | None],
    ) -> None:
        assert parse_compensation(summary) == expected

    def test_never_raises_on_garbage(self) -> None:
        """Adapter contract: parse_compensation must never raise."""
        for garbage in ("\x00", "$$$", "K$K$", "$$K", "0.0.0", "—"):
            parse_compensation(garbage)  # just must not raise
