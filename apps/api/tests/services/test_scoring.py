"""Unit tests for services/scoring.py (PR #56).

All tests are pure (no DB, no LLM). The composite scoring function is
deterministic given (posting, profile, tier) — these tests pin the
behaviour of each feature extractor and a handful of full-composite
fixtures spanning the low/mid/high score range.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from job_assist.db.enums import RoleFamily, SeniorityLevel
from job_assist.db.models.job_posting import JobPosting
from job_assist.db.models.operator_profile import OperatorProfile
from job_assist.services.scoring import (
    ADJACENT_FAMILIES,
    ANNUAL_HOURS,
    PREFERRED_FAMILIES,
    SCORER_VERSION,
    bucket_for_score,
    score_breakdown,
    score_geo,
    score_posting,
    score_role_family,
    score_salary,
    score_seniority,
    score_tier,
)

# ── Constants + version pins ────────────────────────────────────────────────


def test_scorer_version_is_non_empty() -> None:
    assert SCORER_VERSION
    assert isinstance(SCORER_VERSION, str)


def test_scorer_version_is_v1() -> None:
    """If the algorithm changes, bump the version string. This test fires
    when someone forgets the bump."""
    assert SCORER_VERSION == "v1_heuristic"


def test_preferred_and_adjacent_families_are_disjoint() -> None:
    """A family can't be both primary and adjacent. PR #56 Decision A2
    hardcodes both sets in scoring.py; this is the regression guard."""
    assert PREFERRED_FAMILIES.isdisjoint(ADJACENT_FAMILIES)


def test_annual_hours_is_us_full_time_constant() -> None:
    """Decision C reinforcement: 2080 = 40 hr/week * 52 weeks."""
    assert ANNUAL_HOURS == 2080


# ── score_role_family ───────────────────────────────────────────────────────


def test_score_role_family_preferred_returns_100() -> None:
    assert score_role_family(RoleFamily.product_management.value) == 100
    assert score_role_family(RoleFamily.product_owner.value) == 100


def test_score_role_family_adjacent_returns_60() -> None:
    assert score_role_family(RoleFamily.product_marketing.value) == 60
    assert score_role_family(RoleFamily.program_management.value) == 60


def test_score_role_family_other_returns_10() -> None:
    """``other`` is the hard penalty — likely a non-PM role mis-classified."""
    assert score_role_family(RoleFamily.other.value) == 10


def test_score_role_family_none_returns_defensive_40() -> None:
    """role_family is NOT NULL on job_posting, but be defensive."""
    assert score_role_family(None) == 40


# ── score_seniority ─────────────────────────────────────────────────────────


def test_score_seniority_in_included_set_returns_100() -> None:
    s = score_seniority(SeniorityLevel.senior_pm.value, ["senior_pm", "lead_pm"])
    assert s == 100


def test_score_seniority_out_of_included_set_returns_30() -> None:
    s = score_seniority(SeniorityLevel.intern.value, ["senior_pm", "lead_pm"])
    assert s == 30


def test_score_seniority_unknown_returns_neutral_50() -> None:
    """Unknown seniority should surface for triage, not get scored harshly."""
    s = score_seniority(SeniorityLevel.unknown.value, ["senior_pm"])
    assert s == 50


def test_score_seniority_no_preference_returns_70() -> None:
    """NULL or empty included_levels means the operator hasn't filtered."""
    assert score_seniority(SeniorityLevel.senior_pm.value, None) == 70
    assert score_seniority(SeniorityLevel.senior_pm.value, []) == 70


# ── score_salary ────────────────────────────────────────────────────────────


def test_score_salary_inside_band_returns_100() -> None:
    s = score_salary(
        salary_min=120_000,
        salary_max=180_000,
        salary_currency="USD",
        salary_period="annual",
        floor_usd=100_000,
        ceiling_usd=200_000,
    )
    assert s == 100


def test_score_salary_below_floor_returns_30() -> None:
    s = score_salary(
        salary_min=60_000,
        salary_max=70_000,
        salary_currency="USD",
        salary_period="annual",
        floor_usd=100_000,
        ceiling_usd=200_000,
    )
    assert s == 30


def test_score_salary_above_ceiling_returns_80() -> None:
    s = score_salary(
        salary_min=300_000,
        salary_max=400_000,
        salary_currency="USD",
        salary_period="annual",
        floor_usd=100_000,
        ceiling_usd=200_000,
    )
    assert s == 80


def test_score_salary_null_returns_neutral_60() -> None:
    s = score_salary(None, None, None, None, 100_000, 200_000)
    assert s == 60


def test_score_salary_non_usd_returns_neutral_60() -> None:
    """We don't FX-convert — non-USD postings get the neutral bucket."""
    s = score_salary(120_000, 180_000, "EUR", "annual", 100_000, 200_000)
    assert s == 60


def test_score_salary_hourly_annualized_via_2080() -> None:
    """$50/hr * 2080 = $104k → inside a $100k-$200k band."""
    s = score_salary(
        salary_min=50,
        salary_max=60,
        salary_currency="USD",
        salary_period="hourly",
        floor_usd=100_000,
        ceiling_usd=200_000,
    )
    assert s == 100


def test_score_salary_uses_max_when_available_falls_back_to_min() -> None:
    """When max is NULL the extractor falls back to min."""
    s = score_salary(
        salary_min=150_000,
        salary_max=None,
        salary_currency="USD",
        salary_period="annual",
        floor_usd=100_000,
        ceiling_usd=200_000,
    )
    assert s == 100


def test_score_salary_no_ceiling_skips_upper_check() -> None:
    """Ceiling NULL means 'no upper bound' — over-band → still 100."""
    s = score_salary(300_000, 400_000, "USD", "annual", 100_000, None)
    assert s == 100


# ── score_tier ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "expected"),
    [(1, 100), (2, 80), (3, 60), (4, 40)],
)
def test_score_tier_known_tiers(tier: int, expected: int) -> None:
    assert score_tier(tier) == expected


def test_score_tier_none_returns_neutral_50() -> None:
    """Posting has no matched target_company (OUTER JOIN NULL)."""
    assert score_tier(None) == 50


def test_score_tier_out_of_range_returns_neutral_50() -> None:
    """Defensive: a T5 or T0 shouldn't crash."""
    assert score_tier(5) == 50
    assert score_tier(0) == 50


# ── score_geo ───────────────────────────────────────────────────────────────


def test_score_geo_remote_matches_remote_whitelist_entry() -> None:
    s = score_geo(
        locations_normalized=[{"remote_type": "remote"}],
        geo_whitelist=["Remote", "New York"],
    )
    assert s == 100


def test_score_geo_city_substring_match() -> None:
    """Operator's 'NYC' should match posting's 'New York, NY' via the
    two-sided substring check."""
    locations = [{"city": "New York", "state": "NY", "country": "US", "remote_type": "onsite"}]
    s = score_geo(locations, ["New York"])
    assert s == 100


def test_score_geo_no_match_returns_30() -> None:
    locations = [{"city": "Helsinki", "remote_type": "onsite"}]
    s = score_geo(locations, ["New York", "Remote"])
    assert s == 30


def test_score_geo_empty_locations_returns_neutral_50() -> None:
    assert score_geo([], ["Remote"]) == 50
    assert score_geo(None, ["Remote"]) == 50


def test_score_geo_empty_whitelist_returns_neutral_70() -> None:
    """Operator hasn't expressed preferences → don't penalize."""
    locations = [{"city": "Helsinki", "remote_type": "onsite"}]
    assert score_geo(locations, []) == 70


# ── score_posting (composite) ───────────────────────────────────────────────


def _make_posting(**overrides: Any) -> JobPosting:
    """Build a JobPosting with the canonical NOT NULL fields populated.

    Mirrors the existing factory in tests/test_read_endpoints.py — every
    NOT NULL column on job_posting must be present.
    """
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    defaults: dict[str, Any] = {
        "canonical_company_name": "TestCo",
        "target_company_id": None,
        "normalized_title": "senior product manager",
        "raw_title": "Senior Product Manager",
        "jd_text": "JD body.",
        "jd_text_hash": f"{'0' * 54}{suffix}",
        "content_hash": f"hash-{suffix}",
        "first_seen_at": now,
        "last_seen_at": now,
        "role_family": RoleFamily.product_management.value,
        "seniority_level": SeniorityLevel.senior_pm.value,
        "remote_type": "remote",
        "salary_min": 150_000,
        "salary_max": 200_000,
        "salary_currency": "USD",
        "salary_period": "annual",
        "locations_normalized": [{"remote_type": "remote"}],
    }
    defaults.update(overrides)
    return JobPosting(**defaults)


def _make_profile(**overrides: Any) -> OperatorProfile:
    now = datetime.now(tz=UTC)
    defaults: dict[str, Any] = {
        "id": 1,
        "looking_for_text": "PM roles",
        "role_keywords": [],
        "geo_whitelist": ["Remote", "Des Moines", "New York", "Austin"],
        "salary_floor_usd": 100_000,
        "salary_ceiling_usd": 250_000,
        "applicant_cap": 500,
        "seniority_levels_included": ["senior_pm", "lead_pm"],
        "staffing_firm_blocklist": [],
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return OperatorProfile(**defaults)


def test_score_posting_full_match_is_high() -> None:
    """All five features at 100 / tier T1 = 100 → composite ≈ 100."""
    posting = _make_posting()
    profile = _make_profile()
    score = score_posting(posting, profile, tier=1)
    # role 100 (25%) + sen 100 (25%) + sal 100 (15%) + tier 100 (15%) + geo 100 (20%) = 100
    assert score == 100


def test_score_posting_total_mismatch_is_low() -> None:
    """Hard penalty across every feature."""
    posting = _make_posting(
        role_family=RoleFamily.other.value,
        seniority_level=SeniorityLevel.intern.value,
        salary_min=40_000,
        salary_max=50_000,
        salary_currency="USD",
        salary_period="annual",
        locations_normalized=[{"city": "Helsinki", "remote_type": "onsite"}],
    )
    profile = _make_profile()
    score = score_posting(posting, profile, tier=4)
    # role 10 (25%) + sen 30 (25%) + sal 30 (15%) + tier 40 (15%) + geo 30 (20%)
    # = 2.5 + 7.5 + 4.5 + 6 + 6 = 26.5 → 26 (Python ``round()`` uses
    # banker's rounding: round-half-to-even).
    assert score == 26


def test_score_posting_typical_mid_range() -> None:
    """Adjacent role family, neutral salary (NULL), tier 2, remote match."""
    posting = _make_posting(
        role_family=RoleFamily.program_management.value,
        seniority_level=SeniorityLevel.senior_pm.value,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period="unknown",
    )
    profile = _make_profile()
    score = score_posting(posting, profile, tier=2)
    # role 60 (25%) + sen 100 (25%) + sal 60 (15%) + tier 80 (15%) + geo 100 (20%)
    # = 15 + 25 + 9 + 12 + 20 = 81
    assert score == 81


def test_score_posting_is_deterministic() -> None:
    """Same inputs → same output, always."""
    posting = _make_posting()
    profile = _make_profile()
    a = score_posting(posting, profile, tier=1)
    b = score_posting(posting, profile, tier=1)
    c = score_posting(posting, profile, tier=1)
    assert a == b == c


def test_score_posting_clamps_to_0_100() -> None:
    """Defensive: composite arithmetic never escapes [0, 100]."""
    posting = _make_posting()
    profile = _make_profile()
    score = score_posting(posting, profile, tier=1)
    assert 0 <= score <= 100


def test_score_breakdown_returns_five_features() -> None:
    """The debug breakdown must surface all five named features."""
    posting = _make_posting()
    profile = _make_profile()
    parts = score_breakdown(posting, profile, tier=1)
    assert set(parts.keys()) == {"role_family", "seniority", "salary", "tier", "geo"}
    for value in parts.values():
        assert 0 <= value <= 100


def test_score_breakdown_matches_composite_via_weighted_sum() -> None:
    """The composite must equal round(weighted_sum_of_breakdown). Locks
    the breakdown function against drift from score_posting."""
    posting = _make_posting()
    profile = _make_profile()
    parts = score_breakdown(posting, profile, tier=2)
    composite = score_posting(posting, profile, tier=2)
    # Weights from the scoring module; this assertion treats them as
    # an external contract — if the weights change, this test fires.
    weighted = (
        parts["role_family"] * 25
        + parts["seniority"] * 25
        + parts["salary"] * 15
        + parts["tier"] * 15
        + parts["geo"] * 20
    ) / 100.0
    assert composite == round(weighted)


# ── bucket_for_score ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (None, "unscored"),
        (0, "0-19"),
        (19, "0-19"),
        (20, "20-39"),
        (39, "20-39"),
        (40, "40-59"),
        (59, "40-59"),
        (60, "60-79"),
        (79, "60-79"),
        (80, "80-100"),
        (100, "80-100"),
    ],
)
def test_bucket_for_score_thresholds(score: int | None, expected: str) -> None:
    assert bucket_for_score(score) == expected
