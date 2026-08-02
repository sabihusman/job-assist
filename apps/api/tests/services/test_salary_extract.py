"""Unit tests for the Comp-block salary parser (D-SALARY-EXTRACT).

Pure-function tests — no DB, no network. Every pattern below is pinned from
a REAL Comp block in triage-export-20260801-192855 (company named in the
test), so a regression here means a real corpus row stops parsing.
"""

from __future__ import annotations

import pytest

from job_assist.services.salary_extract import ParsedSalary, parse_salary_from_summary


def _summary(comp_block: str) -> str:
    """Wrap a Comp block in a realistic jd_summary_markdown skeleton."""
    return (
        "**Scope**: Own the roadmap for the platform.\n\n"
        f"**Comp**: {comp_block}\n\n"
        "**Location**: Not specified.\n\n"
        "**Ambiguities**:\n* None noted.\n"
    )


# ── Single bands ──────────────────────────────────────────────────────────────


def test_single_band_with_year_suffix_msci() -> None:
    p = parse_salary_from_summary(
        _summary("$181,000 - $235,000 / year plus eligible for annual bonus.")
    )
    assert p == ParsedSalary(181_000, 235_000, "USD", False)


def test_decimal_form_axos() -> None:
    p = parse_salary_from_summary(
        _summary(
            "$70,000.00 - $115,000.00 annually, plus a 10% target for annual "
            "discretionary cash bonus and 10% target for RSUs."
        )
    )
    assert p == ParsedSalary(70_000, 115_000, "USD", False)


def test_k_suffix_notion() -> None:
    p = parse_salary_from_summary(
        _summary(
            "Estimated base salary range of $250k - $280k per year for San Francisco-based roles."
        )
    )
    assert p == ParsedSalary(250_000, 280_000, "USD", False)


def test_usd_token_between_floor_and_dash_john_hancock() -> None:
    """' USD ' between the numbers must not split the band into two singles
    (the collapsed min==max bug caught during corpus validation)."""
    p = parse_salary_from_summary(
        _summary("$107,450.00 USD - $199,550.00 USD annually, plus incentive programs.")
    )
    assert p == ParsedSalary(107_450, 199_550, "USD", False)


# ── Multi-band: lowest band wins, that band's max rides along ─────────────────


def test_per_city_bullets_capital_one_lowest_band() -> None:
    p = parse_salary_from_summary(
        _summary(
            "\n*   Chicago, IL: $149,800 - $171,000\n"
            "*   McLean, VA: $164,800 - $188,100\n"
            "*   New York, NY: $179,700 - $205,100\n"
            "*   Richmond, VA: $149,800 - $171,000"
        )
    )
    assert p is not None
    # Lowest min (149,800) with THAT band's max (171,000) — never NY's 205,100.
    assert (p.salary_min, p.salary_max) == (149_800, 171_000)


def test_tier_list_drata_lowest_tier() -> None:
    p = parse_salary_from_summary(
        _summary(
            "\n*   Tier 1: $108,100 - $133,600\n"
            "*   Tier 2: $97,300 - $120,200\n"
            "*   Tier 3: $86,500 - $106,900\n"
            "*   Includes stock equity (RSUs)"
        )
    )
    assert p is not None
    assert (p.salary_min, p.salary_max) == (86_500, 106_900)


def test_inline_two_band_sentence_adyen() -> None:
    p = parse_salary_from_summary(
        _summary(
            "Annual base salary range of $258,000 - $348,000 in San Francisco "
            "and $235,000 - $317,000 in Chicago, plus RSUs."
        )
    )
    assert p is not None
    assert (p.salary_min, p.salary_max) == (235_000, 317_000)


def test_no_cross_band_min_max_merge() -> None:
    """The policy's explicit negative: lowest min + highest max across
    DIFFERENT bands must never be combined."""
    p = parse_salary_from_summary(_summary("\n* A: $100,000 - $120,000\n* B: $150,000 - $300,000"))
    assert p is not None
    assert (p.salary_min, p.salary_max) == (100_000, 120_000)


# ── OTE ───────────────────────────────────────────────────────────────────────


def test_ote_phrasing_notion() -> None:
    p = parse_salary_from_summary(
        _summary(
            "For roles in San Francisco or New York City, the estimated total "
            "on-target earnings (base + incentive) range from $180,000 - $216,000 per year."
        )
    )
    assert p == ParsedSalary(180_000, 216_000, "USD", False)


def test_ote_abbreviation_drata() -> None:
    p = parse_salary_from_summary(
        _summary(
            "On-Target Earnings (OTE) between $75,000 - $80,000, including "
            "base salary, variable compensation, benefits, and stock (RSUs)."
        )
    )
    assert p == ParsedSalary(75_000, 80_000, "USD", False)


# ── Hourly ────────────────────────────────────────────────────────────────────


def test_hourly_bands_justworks_lowest_band_annualized() -> None:
    """Two hourly bands → lowest annualized band, x2080, conversion recorded."""
    p = parse_salary_from_summary(
        _summary(
            "NYC-metro area: $32.22 - $39.19 per hour. "
            "Outside NYC-metro area: $30.29 - $36.54 per hour."
        )
    )
    assert p is not None
    assert p.annualized_from_hourly is True
    assert p.salary_min == round(30.29 * 2080)
    assert p.salary_max == round(36.54 * 2080)


# ── None cases ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "comp",
    [
        "Not stated.",
        "Not stated (competitive total rewards package discussed during hiring).",
        "Not stated (eligible for bonus/incentive opportunities)",
    ],
)
def test_not_stated_variants_return_none(comp: str) -> None:
    assert parse_salary_from_summary(_summary(comp)) is None


def test_no_comp_heading_returns_none() -> None:
    assert parse_salary_from_summary("**Scope**: Own things. $150,000 - $200,000") is None


def test_none_and_empty_input_return_none() -> None:
    assert parse_salary_from_summary(None) is None
    assert parse_salary_from_summary("") is None
    assert parse_salary_from_summary("   ") is None


def test_figures_outside_comp_block_are_ignored() -> None:
    """Only the Comp block is read — a dollar figure in Scope or Ambiguities
    must not masquerade as a band."""
    md = (
        "**Scope**: Manage the $2,000,000 - $3,000,000 budget.\n\n"
        "**Comp**: Not stated.\n\n"
        "**Ambiguities**:\n* Bonus up to $50,000 - $60,000 possible.\n"
    )
    assert parse_salary_from_summary(md) is None


def test_percent_and_garbled_figures_rejected_by_sanity_bounds() -> None:
    # Magnitude bounds: $5 isn't an annual salary; ratio bound: 100->700k spread.
    assert parse_salary_from_summary(_summary("$5 - $9 referral bonus per signup")) is None
    assert parse_salary_from_summary(_summary("$100,000 - $700,000")) is None


def test_single_value_becomes_min_equals_max() -> None:
    p = parse_salary_from_summary(_summary("$210,000 base."))
    assert p == ParsedSalary(210_000, 210_000, "USD", False)


# ── Review-workflow findings (adversarial verification pass) ─────────────────


@pytest.mark.parametrize(
    ("comp", "expected_min", "expected_max"),
    [
        # Sign-on bonus must not hijack the lowest-band policy.
        ("$149,800 - $171,000 plus a $25,000 sign-on bonus.", 149_800, 171_000),
        # Relocation stipend likewise.
        (
            "$181,000 - $235,000 / year. Relocation assistance of $15,000 available.",
            181_000,
            235_000,
        ),
        # A variable-component RANGE is still a range — the lowest-min policy
        # legitimately picks the base band only because it has the lower min?
        # No: variable 30k-36k has the LOWER min. This is the accepted edge of
        # the range-preference fix: range-vs-range can't be disambiguated
        # without semantics. Pin the outcome so a future change is deliberate.
        (
            "OTE of $150,000 - $180,000 including a variable component of $30,000 - $36,000.",
            30_000,
            36_000,
        ),
        # 401(k) match single figure loses to the real band.
        ("$150,000 - $180,000 plus 401(k) match up to $10,000 annually.", 150_000, 180_000),
    ],
)
def test_single_figures_do_not_hijack_lowest_band(
    comp: str, expected_min: int, expected_max: int
) -> None:
    """Review finding: lone $-figures (bonus/relocation/match) entered as
    min==max singles and beat the real band under lowest-min. Real ranges now
    take precedence over single values whenever any range exists."""
    p = parse_salary_from_summary(_summary(comp))
    assert p is not None
    assert (p.salary_min, p.salary_max) == (expected_min, expected_max)


def test_prose_starting_with_cad_does_not_flip_currency() -> None:
    """Review finding: '. Cadence: ...' after a band tripped the raw 5-char
    'CAD' substring check inherited from normalization."""
    p = parse_salary_from_summary(_summary("$150,000 - $180,000. Cadence: annual reviews."))
    assert p is not None
    assert p.currency == "USD"


def test_real_cad_token_still_detected() -> None:
    p = parse_salary_from_summary(_summary("$120,000 - $150,000 CAD annually."))
    assert p is not None
    assert p.currency == "CAD"


def test_bold_sublabels_do_not_truncate_comp_block() -> None:
    """Review finding: a sub-labeled comp value ('**Base**: ...') must not
    terminate the block — only the enrichment template's own next section."""
    md = "**Comp**:\n**Base**: $150,000 - $180,000\n**Bonus**: 10%\n\n**Location**: Remote.\n"
    p = parse_salary_from_summary(md)
    assert p is not None
    assert (p.salary_min, p.salary_max) == (150_000, 180_000)


def test_up_to_phrasing_pins_min_equals_max_known_limitation() -> None:
    """Review finding, accepted as a KNOWN LIMITATION: 'Up to $200,000' has
    no stated floor, but ParsedSalary records min==max==200,000. score_salary
    only reads the max, so scoring is unaffected; the export's
    salary_parsed_min overstates the floor for ceiling-only phrasing. Pinned
    so any future change is deliberate."""
    p = parse_salary_from_summary(_summary("Up to $200,000 per year."))
    assert p == ParsedSalary(200_000, 200_000, "USD", False)
