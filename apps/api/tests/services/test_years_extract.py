"""Unit tests for the Hard-requirements years detector (D5-OBSERVE).

Pure-function tests — no DB, no network. Patterns pinned from REAL Hard
requirements blocks in triage-export-20260801-192855 (company named per
test). Observe-only: nothing here touches scoring.
"""

from __future__ import annotations

import pytest

from job_assist.services.years_extract import parse_years_requirement


def _summary(hard_req_block: str) -> str:
    return (
        "**Scope**: Own the roadmap.\n\n"
        f"**Hard requirements**:\n{hard_req_block}\n\n"
        "**Nice-to-haves**:\n*   12+ years preferred.\n\n"
        "**Comp**: Not stated.\n\n"
        "**Location**: Remote.\n"
    )


# ── The three pinned clause forms ────────────────────────────────────────────


def test_n_plus_years_msci() -> None:
    r = parse_years_requirement(
        _summary("*   15+ years of experience in product/service/program management.")
    )
    assert r is not None
    assert r["min_years_required"] == 15
    assert r["years_domain"] == "of experience in product/service/program management"
    assert "15+ years" in r["source_clause"]


def test_at_least_n_years_capital_one() -> None:
    r = parse_years_requirement(
        _summary(
            "*   Bachelor's or Master's Degree in a quantitative field, Business, or Marketing.\n"
            "*   OR Bachelor's degree in any field and at least 3 years of Product Management experience."
        )
    )
    assert r is not None
    assert r["min_years_required"] == 3
    assert r["years_domain"] == "of Product Management experience"
    assert r["education_substitution_available"] is True


def test_minimum_of_n_years() -> None:
    r = parse_years_requirement(_summary("*   Minimum of 7 years in fintech product roles."))
    assert r is not None
    assert r["min_years_required"] == 7
    assert r["years_domain"] == "in fintech product roles"


def test_range_yields_lower_bound_q2() -> None:
    """Directive-pinned: '5-8 years' must yield 5."""
    r = parse_years_requirement(_summary("*   5-8 years as a Product Owner or Business Analyst."))
    assert r is not None
    assert r["min_years_required"] == 5
    assert r["years_domain"] == "as a Product Owner or Business Analyst"


def test_en_dash_range_also_lower_bound() -> None:
    # Escaped en dash (U+2013) keeps the source ASCII (ruff RUF001), same
    # convention as normalization.py's dash handling.
    r = parse_years_requirement(_summary(f"*   5{chr(0x2013)}8 years of product ownership."))
    assert r is not None
    assert r["min_years_required"] == 5


def test_singular_year_principal_financial() -> None:
    """'1+ year' (singular) parses; multiple clauses → the LOWEST wins and
    that clause's qualifier is the domain (Principal Financial, live)."""
    r = parse_years_requirement(
        _summary(
            "*   2+ years of business, technology, or product management experience (or equivalent).\n"
            "*   1+ year working in a highly collaborative, fast-paced environment with engineering teams."
        )
    )
    assert r is not None
    assert r["min_years_required"] == 1
    assert r["years_domain"].startswith("working in a highly collaborative")
    assert "1+ year" in r["source_clause"]


# ── Multiple clauses / lowest-wins ───────────────────────────────────────────


def test_lowest_clause_wins_with_its_own_domain() -> None:
    r = parse_years_requirement(
        _summary(
            "*   10+ years of leadership experience.\n"
            "*   At least 4 years of financial services experience."
        )
    )
    assert r is not None
    assert r["min_years_required"] == 4
    assert r["years_domain"] == "of financial services experience"


# ── None cases ────────────────────────────────────────────────────────────────


def test_no_years_clause_returns_none_msci_style() -> None:
    """A Hard requirements block with no clause in the three pinned forms →
    None. No inference from elsewhere in the summary."""
    r = parse_years_requirement(
        _summary(
            "*   Deep and commercial understanding of the commercial real estate industry.\n"
            "*   Working knowledge of both debt and equity sides of real assets."
        )
    )
    assert r is None


def test_bare_n_years_deliberately_does_not_match() -> None:
    """'6 years of experience' (no +/minimum/at least/range) is NOT one of
    the pinned forms — it must return None and surface in the unmatched
    report instead of being silently absorbed (JPMC, live corpus)."""
    r = parse_years_requirement(
        _summary("*   6 years of experience in product management, digital product development.")
    )
    assert r is None


def test_years_outside_hard_requirements_ignored() -> None:
    """The Nice-to-haves '12+ years preferred' in the fixture skeleton must
    never leak in — only the Hard requirements block is read."""
    r = parse_years_requirement(_summary("*   Bachelor's degree required."))
    assert r is None


def test_no_block_and_empty_inputs_return_none() -> None:
    assert parse_years_requirement(None) is None
    assert parse_years_requirement("") is None
    assert parse_years_requirement("**Scope**: Own it. 10+ years required.") is None


# ── education_substitution_available ─────────────────────────────────────────


def test_edu_substitution_inline_or_capital_one_variant() -> None:
    """The inline ', OR a Bachelor's...' single-bullet variant (live)."""
    r = parse_years_requirement(
        _summary(
            "*   Bachelor's or Master's Degree in a quantitative field, Business, or Marketing, "
            "OR a Bachelor's degree in any field and at least 3 years of Product Management experience."
        )
    )
    assert r is not None
    assert r["education_substitution_available"] is True


def test_no_edu_substitution_when_degree_and_years_are_independent() -> None:
    """Axos shape (live): a degree bullet AND a years bullet with no OR
    joining them — the flag must stay False."""
    r = parse_years_requirement(
        _summary(
            "*   Bachelor's degree\n"
            "*   5+ years as a Product Owner/Manager with backend or platform engineering team ownership"
        )
    )
    assert r is not None
    assert r["min_years_required"] == 5
    assert r["education_substitution_available"] is False


def test_lowercase_prose_or_alone_does_not_set_flag() -> None:
    """A lowercase list-comma 'or' between degree words must not trip the
    case-sensitive OR branch when no 'or at least' joins degree to years."""
    r = parse_years_requirement(
        _summary(
            "*   Bachelor's degree in Business, Economics, or Marketing.\n"
            "*   7+ years of product experience."
        )
    )
    assert r is not None
    assert r["education_substitution_available"] is False


@pytest.mark.parametrize(
    "clause,expected",
    [
        # "or at least" joins degree → years (lowercase form).
        ("*   Bachelor's degree, or at least 6 years of equivalent experience.", True),
        # OR joins in the other bullet (Capital One two-bullet shape).
        (
            "*   Master's Degree in Business.\n*   OR at least 3 years of PM experience.",
            True,
        ),
    ],
)
def test_edu_substitution_variants(clause: str, expected: bool) -> None:
    r = parse_years_requirement(_summary(clause))
    assert r is not None
    assert r["education_substitution_available"] is expected
