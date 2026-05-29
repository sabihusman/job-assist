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


class TestParseCompensationPrecision:
    """Failure modes from feeding the full Greenhouse JD body (PR #80) to a
    parser designed for clean comp summaries. Real source text from the
    production audit (salary-parser-precision)."""

    def test_range_is_always_ordered_min_le_max(self) -> None:
        # Ceiling stated before floor → must still come out ordered.
        assert parse_compensation("$236,200 - $189,000 USD") == (
            189_000,
            236_200,
            "USD",
            "annual",
        )

    def test_en_dash_ceiling_without_dollar_is_captured(self) -> None:
        """The ceiling "236,200" has no $ of its own. The old parser missed it
        and grabbed the next $-anchored number instead."""
        assert parse_compensation("$189,000–236,200 USD") == (
            189_000,
            236_200,
            "USD",
            "annual",
        )

    def test_multi_currency_scopes_to_usd_not_cross_currency_floors(self) -> None:
        """Real Mercury JD: US range + CAD range. Must return the USD range
        (both ends), NOT pair the US floor with the CAD floor (which inverted
        to 189,000 > 178,600)."""
        jd = (
            "compensation (any location): $189,000–236,200 USD for US "
            "employees outside SF, and $178,600–223,200 CAD for Canada."
        )
        assert parse_compensation(jd) == (189_000, 236_200, "USD", "annual")

    def test_garbled_high_figure_skipped_for_next_real_range(self) -> None:
        """Real Mercury JD: a garbled $142,400,000 leads, the real SF range
        $128,200-$160,200 follows. Reject the >$1M garble, take the real one."""
        jd = "San Francisco: $142,400,000 - $178,000 outside SF: $128,200-$160,200"
        assert parse_compensation(jd) == (128_200, 160_200, "USD", "annual")

    def test_implausible_ratio_rejected_to_none(self) -> None:
        """Real Brex typo: "$147,00" parses to 14,700 → 8x spread vs 117,600.
        Reject rather than emit a nonsense range; no other range → None."""
        assert parse_compensation("salary is $117,600 - $147,00 CAD") == (
            None,
            None,
            None,
            None,
        )

    def test_over_one_million_rejected(self) -> None:
        assert parse_compensation("$142,400,000 base") == (None, None, None, None)

    def test_stray_small_dollar_amount_ignored(self) -> None:
        """A "$10 fee" mention must not become a salary; the real range wins."""
        jd = "We saved customers $10 in fees. Comp: $150,000–$190,000."
        assert parse_compensation(jd) == (150_000, 190_000, "USD", "annual")

    def test_real_range_preferred_over_lone_number(self) -> None:
        """A lone $-number earlier in the text shouldn't beat a real range."""
        jd = "Equity grant around $90,000. Base pay: $160,000 - $200,000."
        assert parse_compensation(jd) == (160_000, 200_000, "USD", "annual")

    def test_clean_single_currency_range_unchanged(self) -> None:
        # Regression guard: the common correct case is untouched.
        assert parse_compensation("$180,000 - $275,000 USD") == (
            180_000,
            275_000,
            "USD",
            "annual",
        )
