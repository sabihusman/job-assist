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
    _DISGUISED_SENIOR_CAP,
    _WEIGHTS,
    ADJACENT_FAMILIES,
    ANALYST_FAMILIES,
    ANALYST_GATE_CAP,
    ANNUAL_HOURS,
    MAX_APPLIED_BOOST,
    PREFERRED_FAMILIES,
    ROLE_GATE_CAP,
    SCORER_VERSION,
    AppliedBasis,
    bucket_for_score,
    display_tier,
    is_disguised_senior,
    score_breakdown,
    score_geo,
    score_posting,
    score_posting_decomposed,
    score_role_family,
    score_salary,
    score_semantic_fit,
    score_seniority,
    score_tier,
)

# ── Constants + version pins ────────────────────────────────────────────────


def test_scorer_version_is_non_empty() -> None:
    assert SCORER_VERSION
    assert isinstance(SCORER_VERSION, str)


def test_scorer_version_is_v2_semantic() -> None:
    """If the algorithm changes, bump the version string. This test fires
    when someone forgets the bump."""
    assert SCORER_VERSION == "v2_semantic"


def test_preferred_and_adjacent_families_are_disjoint() -> None:
    """A family can't be both primary and adjacent. PR #56 Decision A2
    hardcodes both sets in scoring.py; this is the regression guard."""
    assert PREFERRED_FAMILIES.isdisjoint(ADJACENT_FAMILIES)


def test_analyst_families_disjoint_from_preferred_and_adjacent() -> None:
    """business_analyst/financial_analyst expansion: ANALYST_FAMILIES must
    not overlap either existing set — it's a third, distinct bucket."""
    assert ANALYST_FAMILIES.isdisjoint(PREFERRED_FAMILIES)
    assert ANALYST_FAMILIES.isdisjoint(ADJACENT_FAMILIES)
    assert {
        RoleFamily.business_analyst.value,
        RoleFamily.financial_analyst.value,
    } == ANALYST_FAMILIES


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


def test_score_role_family_analyst_returns_75() -> None:
    """business_analyst/financial_analyst are acceptable-but-discounted —
    between ADJACENT's 60 and PREFERRED's 100."""
    assert score_role_family(RoleFamily.business_analyst.value) == 75
    assert score_role_family(RoleFamily.financial_analyst.value) == 75


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


def test_score_posting_typical_mid_range_capped_by_gate() -> None:
    """Adjacent role family, neutral salary (NULL), tier 2, remote match.

    Bestiary 5.21: the raw weighted composite here is 81, but
    program_management is NOT a PREFERRED family, so the hard gate caps it
    at 40 — a wrong-role posting must not outrank genuine PM roles.
    """
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
    # Raw: role 60 (25%) + sen 100 (25%) + sal 60 (15%) + tier 80 (15%)
    # + geo 100 (20%) = 81 → gate caps to 40.
    assert score == 40


# ── role_family hard gate (Bestiary 5.21) ────────────────────────────────────


@pytest.mark.parametrize(
    "family",
    [
        RoleFamily.program_management.value,
        RoleFamily.product_marketing.value,
        RoleFamily.other.value,
    ],
)
def test_score_posting_gate_caps_non_preferred_at_40(family: str) -> None:
    """A non-PREFERRED role at Tier-1, in-geo, in-band, with the operator's
    seniority — every weighted factor maxed — must still cap at 40. Proves
    the gate fires on a HIGH raw composite, not just an already-low one."""
    posting = _make_posting(
        role_family=family,
        seniority_level=SeniorityLevel.senior_pm.value,
        salary_min=150_000,
        salary_max=200_000,
        salary_currency="USD",
        salary_period="annual",
        locations_normalized=[{"remote_type": "remote"}],
    )
    profile = _make_profile()
    score = score_posting(posting, profile, tier=1)
    assert score == 40, f"{family} should be gated to 40, got {score}"


@pytest.mark.parametrize(
    "family",
    [RoleFamily.product_management.value, RoleFamily.product_owner.value],
)
def test_score_posting_gate_leaves_preferred_uncapped(family: str) -> None:
    """PREFERRED families are NOT gated — a strong match scores well above 40."""
    posting = _make_posting(
        role_family=family,
        seniority_level=SeniorityLevel.senior_pm.value,
        salary_min=150_000,
        salary_max=200_000,
        salary_currency="USD",
        salary_period="annual",
        locations_normalized=[{"remote_type": "remote"}],
    )
    profile = _make_profile()
    score = score_posting(posting, profile, tier=1)
    assert score == 100, f"{family} full match should be 100, got {score}"


def test_score_posting_gate_orders_wrong_role_below_weak_pm() -> None:
    """The whole point of the gate: a Tier-1 in-everything wrong-role posting
    must rank BELOW even a weak genuine-PM posting."""
    wrong_role_strong = _make_posting(role_family=RoleFamily.program_management.value)
    weak_pm = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.intern.value,  # out-of-band seniority
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period="unknown",
        locations_normalized=[{"city": "Helsinki", "remote_type": "onsite"}],
    )
    profile = _make_profile()
    wrong_score = score_posting(wrong_role_strong, profile, tier=1)
    pm_score = score_posting(weak_pm, profile, tier=4)
    assert wrong_score == 40
    assert pm_score > wrong_score, (
        f"weak PM ({pm_score}) must outrank a gated wrong-role posting ({wrong_score})"
    )


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


def test_score_breakdown_returns_six_features_plus_flag() -> None:
    """The debug breakdown surfaces the six named numeric features plus
    the ``disguised_senior`` boolean flag (slice 2b adds ``semantic_fit``)."""
    posting = _make_posting()
    profile = _make_profile()
    parts = score_breakdown(posting, profile, tier=1)
    assert set(parts.keys()) == {
        "role_family",
        "seniority",
        "salary",
        "tier",
        "geo",
        "semantic_fit",
        "disguised_senior",
    }
    # The five structured features are 0-100 ints.
    for key in ("role_family", "seniority", "salary", "tier", "geo"):
        assert isinstance(parts[key], int)
        assert 0 <= parts[key] <= 100
    # semantic_fit is None until the row is embedded + calibrated (the fixture
    # has no similarity_score), otherwise a 0-100 int.
    assert parts["semantic_fit"] is None or (
        isinstance(parts["semantic_fit"], int) and 0 <= int(parts["semantic_fit"]) <= 100
    )
    # The flag is a bool, not a weighted score.
    assert isinstance(parts["disguised_senior"], bool)


def test_score_breakdown_matches_composite_via_weighted_sum() -> None:
    """The composite must equal the renormalized weighted MEAN of the
    breakdown over the AVAILABLE features. Locks the breakdown function
    against drift from score_posting (no cap fires for this clean PM fixture)."""
    posting = _make_posting()
    profile = _make_profile()
    parts = score_breakdown(posting, profile, tier=2)
    composite = score_posting(posting, profile, tier=2)
    # Weights from the scoring module, treated as an external contract — if the
    # weights change, this test fires. semantic_fit is omitted + renormalized
    # when absent (no similarity_score on the fixture).
    weights = {
        "role_family": 20,
        "seniority": 20,
        "salary": 15,
        "tier": 10,
        "geo": 15,
        "semantic_fit": 20,
    }
    acc = 0.0
    total = 0
    for key, weight in weights.items():
        value = parts[key]
        if value is None:
            continue
        acc += int(value) * weight
        total += weight
    weighted = acc / total if total else 0.0
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


# ── display_tier (Slice 3 — tier-from-score coalesce) ───────────────────────


def test_display_tier_company_tier_always_wins() -> None:
    """A curated company's pedigree tier is returned verbatim regardless
    of fit_score — even a tier-1 company with a low-scoring posting."""
    assert display_tier(1, 30) == 1
    assert display_tier(4, 95) == 4
    assert display_tier(2, None) == 2


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, 1),
        (80, 1),
        (79, 2),
        (60, 2),
        (59, 3),
        (40, 3),
        (39, 4),
        (0, 4),
    ],
)
def test_display_tier_derives_band_from_score_when_company_tier_null(
    score: int, expected: int
) -> None:
    """Broad shells (company_tier=None) get a band derived from
    fit_score — the inverse of score_tier's bands."""
    assert display_tier(None, score) == expected


def test_display_tier_null_when_both_null() -> None:
    """A broad shell whose posting the score sweep hasn't visited
    (fit_score None) has no derivable tier → None."""
    assert display_tier(None, None) is None


# ── Disguised-senior altitude cap (career-changer correction) ───────────────


def test_is_disguised_senior_pm_with_senior_comp_floor() -> None:
    """PM-family, seniority=pm, USD floor >= $180k → flagged."""
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=185_000,
        salary_max=240_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(p) is True


def test_is_disguised_senior_unknown_seniority_with_senior_floor() -> None:
    """unknown seniority (passes the hard rule) + senior floor → flagged."""
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.unknown.value,
        salary_min=200_000,
        salary_max=260_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(p) is True


def test_disguised_senior_caps_composite_at_55() -> None:
    """A flagged posting that would otherwise score high is capped at 55."""
    profile = _make_profile(seniority_levels_included=["intern", "apm", "pm"])
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=190_000,
        salary_max=250_000,
        salary_currency="USD",
        locations_normalized=[{"remote_type": "remote"}],
    )
    # Sanity: without the cap this would clear 55 (PM family + in-set
    # seniority + decent comp). The cap pulls it to <= 55.
    assert score_posting(p, profile, tier=1) <= 55


# ── False-positive guards (must NOT be flagged) ─────────────────────────────


def test_not_disguised_when_only_max_reaches_180() -> None:
    """A $130k-$180k band has min 130 (plausibly mid) — NOT flagged."""
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=130_000,
        salary_max=180_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(p) is False


def test_not_disguised_when_non_usd() -> None:
    """Non-USD comp never flags — we don't FX-convert."""
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=200_000,
        salary_max=260_000,
        salary_currency="EUR",
    )
    assert is_disguised_senior(p) is False


def test_not_disguised_when_apm() -> None:
    """apm is explicitly junior + wanted — never flagged even at high comp."""
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.apm.value,
        salary_min=200_000,
        salary_max=260_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(p) is False


def test_not_disguised_when_floor_below_threshold() -> None:
    """A genuine junior-but-well-paid PM at a $150k floor is spared."""
    p = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=150_000,
        salary_max=190_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(p) is False


def test_not_disguised_when_not_product_management() -> None:
    """A non-PM family is out of scope for this cap (handled by the family gate)."""
    p = _make_posting(
        role_family=RoleFamily.program_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=200_000,
        salary_max=260_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(p) is False


def test_breakdown_surfaces_disguised_senior_flag() -> None:
    """score_breakdown exposes the bool flag for debugging/UI."""
    profile = _make_profile()
    flagged = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=190_000,
        salary_currency="USD",
    )
    clean = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=140_000,
        salary_currency="USD",
    )
    assert score_breakdown(flagged, profile, tier=1)["disguised_senior"] is True
    assert score_breakdown(clean, profile, tier=1)["disguised_senior"] is False


# ── semantic_fit feature (slice 2b) ──────────────────────────────────────────


def test_score_semantic_fit_passthrough_and_clamp() -> None:
    """semantic_fit reads the precomputed 0-100 similarity_score; None stays
    None (absent), out-of-range values clamp into [0, 100]."""
    assert score_semantic_fit(None) is None
    assert score_semantic_fit(0) == 0
    assert score_semantic_fit(50) == 50
    assert score_semantic_fit(100) == 100
    # Defensive clamp (similarity_score should already be 0-100).
    assert score_semantic_fit(150) == 100
    assert score_semantic_fit(-5) == 0


def test_disguised_senior_floor_is_175k() -> None:
    """The disguised-senior cap fires at a $175k floor (retuned from $180k to
    match the operator's profile)."""
    profile = _make_profile()
    # pm-seniority product_management at $175k floor → flagged + capped at 55.
    at_floor = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=175_000,
        salary_max=210_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(at_floor) is True
    assert score_posting(at_floor, profile, tier=1) <= 55
    # Just under the floor → not flagged.
    below = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=174_000,
        salary_max=210_000,
        salary_currency="USD",
    )
    assert is_disguised_senior(below) is False


def test_semantic_fit_omitted_when_absent_renormalizes() -> None:
    """A posting with no similarity_score scores on the structured five,
    renormalized — semantic_fit contributes nothing (no fake signal)."""
    profile = _make_profile()
    posting = _make_posting(similarity_score=None)
    parts = score_breakdown(posting, profile, tier=2)
    assert parts["semantic_fit"] is None
    structured = {"role_family": 20, "seniority": 20, "salary": 15, "tier": 10, "geo": 15}
    weighted = sum(int(parts[k]) * w for k, w in structured.items()) / sum(structured.values())
    assert score_posting(posting, profile, tier=2) == round(weighted)


def test_semantic_fit_blends_in_and_is_directional() -> None:
    """When similarity_score is present it joins the composite at 20%. Use a
    MID-strength PM posting (low tier/geo/salary) so the blend can visibly move
    the score both above and below the absent baseline — a maxed-out fixture
    would clamp at 100 and hide the effect."""
    profile = _make_profile()
    base: dict[str, Any] = {
        "role_family": RoleFamily.product_management.value,  # passes the gate
        "seniority_level": SeniorityLevel.pm.value,
        "salary_min": 80_000,
        "salary_max": 100_000,
        "salary_currency": "USD",
        "locations_normalized": [{"city": "Nowhere"}],  # geo miss
    }
    absent = score_posting(_make_posting(similarity_score=None, **base), profile, tier=4)
    high = score_posting(_make_posting(similarity_score=100, **base), profile, tier=4)
    low = score_posting(_make_posting(similarity_score=0, **base), profile, tier=4)
    assert low < absent < high


def test_role_family_gate_still_caps_at_40_despite_high_semantic_fit() -> None:
    """The hard role_family gate is preserved: a non-PM role with a perfect
    semantic_fit still can't exceed 40 (slice 2b must not breach the gate)."""
    profile = _make_profile()
    non_pm = _make_posting(
        role_family=RoleFamily.program_management.value,
        seniority_level=SeniorityLevel.unknown.value,
        similarity_score=100,
    )
    assert score_posting(non_pm, profile, tier=1) <= 40


# ── business_analyst/financial_analyst three-way gate ────────────────────────


@pytest.mark.parametrize(
    "family",
    [RoleFamily.business_analyst.value, RoleFamily.financial_analyst.value],
)
def test_analyst_family_gate_caps_at_85_not_40(family: str) -> None:
    """Analyst families are acceptable-but-discounted: capped at
    ANALYST_GATE_CAP (85), never dropped all the way to ROLE_GATE_CAP (40)
    like a true non-PM family (program_management, other, ...)."""
    profile = _make_profile()
    posting = _make_posting(
        role_family=family,
        seniority_level=SeniorityLevel.senior_pm.value,
        similarity_score=100,
    )
    score = score_posting(posting, profile, tier=1)
    assert score <= ANALYST_GATE_CAP
    assert score > ROLE_GATE_CAP


def test_analyst_family_gate_does_not_fire_role_gate() -> None:
    """The three-way split: analyst families must NOT set role_family_gate
    (that's reserved for the true non-PM bucket / ROLE_GATE_CAP)."""
    profile = _make_profile()
    posting = _make_posting(role_family=RoleFamily.business_analyst.value, similarity_score=100)
    d = score_posting_decomposed(posting, profile, tier=1)
    assert d.caps["role_family_gate"]["fired"] is False
    assert d.caps["analyst_family_gate"]["fired"] is True
    assert d.caps["analyst_family_gate"]["cap"] == ANALYST_GATE_CAP


def test_preferred_family_remains_uncapped_by_analyst_gate() -> None:
    """PREFERRED_FAMILIES rows must not trip the new analyst gate."""
    profile = _make_profile()
    posting = _make_posting(role_family=RoleFamily.product_management.value, similarity_score=100)
    d = score_posting_decomposed(posting, profile, tier=1)
    assert d.caps["role_family_gate"]["fired"] is False
    assert d.caps["analyst_family_gate"]["fired"] is False


# ── Phase A1: score decomposition (expose, don't alter) ──────────────────────


def _decomp_cases() -> list[tuple[str, JobPosting, int]]:
    """(label, posting, tier) covering: full match, semantic NULL (renormalize),
    non-PM (role gate fires), disguised-senior (soft cap fires)."""
    return [
        ("full_match", _make_posting(similarity_score=100), 1),
        ("semantic_null_renormalize", _make_posting(similarity_score=None), 2),
        (
            "role_gate",
            _make_posting(role_family=RoleFamily.other.value, similarity_score=100),
            1,
        ),
        (
            "analyst_gate",
            _make_posting(role_family=RoleFamily.business_analyst.value, similarity_score=100),
            1,
        ),
        (
            "disguised_senior",
            _make_posting(
                role_family=RoleFamily.product_management.value,
                seniority_level=SeniorityLevel.pm.value,
                salary_min=175_000,
                salary_max=210_000,
                salary_currency="USD",
                similarity_score=100,
            ),
            1,
        ),
    ]


@pytest.mark.parametrize("label,posting,tier", _decomp_cases())
def test_decomposed_final_equals_score_posting(label: str, posting: JobPosting, tier: int) -> None:
    """The refactor is byte-for-byte: score_posting == score_posting_decomposed().final."""
    profile = _make_profile()
    decomp = score_posting_decomposed(posting, profile, tier=tier)
    assert decomp.final == score_posting(posting, profile, tier=tier)


@pytest.mark.parametrize("label,posting,tier", _decomp_cases())
def test_decomposition_reconciles_to_final(label: str, posting: JobPosting, tier: int) -> None:
    """sum(contributions)/total_weight → score_pre_caps → caps → final, and the
    decomposition's final equals the row's fit_score (score_posting)."""
    profile = _make_profile()
    d = score_posting_decomposed(posting, profile, tier=tier)
    # contributions sum / renormalized weight reproduces the pre-cap score.
    recomputed_mean = (sum(d.contributions.values()) / d.total_weight) if d.total_weight else 0.0
    assert recomputed_mean == d.weighted_mean
    assert d.score_pre_caps == max(0, min(100, round(recomputed_mean)))
    # Apply caps the same way score_posting does.
    expected = d.score_pre_caps
    if d.caps["role_family_gate"]["fired"]:
        expected = min(expected, ROLE_GATE_CAP)
    if d.caps["analyst_family_gate"]["fired"]:
        expected = min(expected, ANALYST_GATE_CAP)
    if d.caps["disguised_senior"]["fired"]:
        expected = min(expected, _DISGUISED_SENIOR_CAP)
    assert d.final == expected
    assert d.final == score_posting(posting, profile, tier=tier)
    # contributions are present-only; each is value*weight.
    for key, contrib in d.contributions.items():
        assert contrib == int(d.sub_scores[key]) * d.weights[key]


def test_decomposition_semantic_null_drops_and_renormalizes() -> None:
    profile = _make_profile()
    d = score_posting_decomposed(_make_posting(similarity_score=None), profile, tier=2)
    assert d.sub_scores["semantic_fit"] is None
    assert d.dropped == ["semantic_fit"]
    assert "semantic_fit" not in d.present
    assert d.total_weight == sum(w for k, w in _WEIGHTS.items() if k != "semantic_fit")  # 80
    assert d.total_weight == 80


def test_decomposition_role_gate_cap_fires() -> None:
    profile = _make_profile()
    d = score_posting_decomposed(
        _make_posting(role_family=RoleFamily.other.value, similarity_score=100), profile, tier=1
    )
    assert d.caps["role_family_gate"]["fired"] is True
    assert d.caps["role_family_gate"]["cap"] == 40
    assert d.final <= 40


def test_decomposition_analyst_gate_cap_fires() -> None:
    profile = _make_profile()
    d = score_posting_decomposed(
        _make_posting(role_family=RoleFamily.financial_analyst.value, similarity_score=100),
        profile,
        tier=1,
    )
    assert d.caps["analyst_family_gate"]["fired"] is True
    assert d.caps["analyst_family_gate"]["cap"] == ANALYST_GATE_CAP
    assert d.caps["role_family_gate"]["fired"] is False
    assert d.final <= ANALYST_GATE_CAP


def test_decomposition_disguised_senior_cap_fires() -> None:
    profile = _make_profile()
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=175_000,
        salary_max=210_000,
        salary_currency="USD",
        similarity_score=100,
    )
    d = score_posting_decomposed(posting, profile, tier=1)
    assert d.caps["disguised_senior"]["fired"] is True
    assert d.caps["disguised_senior"]["cap"] == 55
    assert d.final <= 55


def test_decomposition_to_dict_shape_and_version() -> None:
    profile = _make_profile()
    d = score_posting_decomposed(_make_posting(similarity_score=100), profile, tier=1)
    out = d.to_dict()
    assert set(out) == {
        "scorer_version",
        "weights",
        "sub_scores",
        "present",
        "dropped",
        "total_weight",
        "contributions",
        "weighted_mean",
        "score_pre_caps",
        "caps",
        "applied_corpus_boost",
        "final",
    }
    assert out["scorer_version"] == SCORER_VERSION
    assert out["weights"] == dict(_WEIGHTS)
    assert out["final"] == score_posting(_make_posting(similarity_score=100), profile, tier=1)
    # A3: with no basis the boost block is present but inert.
    assert out["applied_corpus_boost"]["boost_points"] == 0
    assert out["applied_corpus_boost"]["n"] is None


# ── Phase A3: surgical applied-corpus boost (Philosophy 2) ───────────────────


def _basis(centroid: list[float], *, n: int = 16, ref: float = 0.884) -> AppliedBasis:
    norm = sum(c * c for c in centroid) ** 0.5
    return AppliedBasis(centroid=centroid, centroid_norm=norm, reference_band=ref, n=n)


# in-target = {apm, pm}; senior_pm/lead_pm OUT (matches the operator's intent).
def _a3_profile(weight: float = 1.0, **overrides: Any) -> OperatorProfile:
    return _make_profile(
        seniority_levels_included=["apm", "pm"],
        applied_corpus_weight=weight,
        **overrides,
    )


def test_a3_weight_zero_is_byte_exact_noop() -> None:
    """Weight 0 ⇒ boost 0 ⇒ final identical to the no-basis score, even with a
    basis + perfect similarity. A1 reconciliation (final==fit_score) holds."""
    profile = _a3_profile(weight=0.0)
    posting = _make_posting(seniority_level=SeniorityLevel.pm.value, jd_embedding=[1.0, 0.0, 0.0])
    basis = _basis([1.0, 0.0, 0.0])
    d = score_posting_decomposed(posting, profile, tier=1, applied_basis=basis)
    assert d.applied_corpus_boost["boost_points"] == 0
    assert d.final == score_posting(posting, profile, tier=1)  # no-basis path


def test_a3_eligibility_gate_disguised_no_boost() -> None:
    """A disguised-senior (capped) row gets NO boost even at high weight + sim —
    stays at its cap (Plaid-PM-style)."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        salary_min=175_000,  # disguised-senior trip
        salary_max=210_000,
        salary_currency="USD",
        similarity_score=100,
        jd_embedding=[1.0, 0.0, 0.0],
    )
    basis = _basis([1.0, 0.0, 0.0])  # sim = 1.0
    d = score_posting_decomposed(posting, profile, tier=1, applied_basis=basis)
    assert d.caps["disguised_senior"]["fired"] is True
    assert d.applied_corpus_boost["eligible"] is False
    assert d.applied_corpus_boost["eligibility"]["not_disguised"] is False
    assert d.applied_corpus_boost["boost_points"] == 0
    assert d.final <= _DISGUISED_SENIOR_CAP


def test_a3_eligibility_gate_analyst_family_no_boost() -> None:
    """An analyst-family (85-capped) row gets NO boost even at high weight +
    perfect sim — the boost must never push it past ANALYST_GATE_CAP, same
    shape as the role-gate and disguised-senior guards."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.business_analyst.value,
        seniority_level=SeniorityLevel.pm.value,
        similarity_score=100,
        jd_embedding=[1.0, 0.0, 0.0],
    )
    basis = _basis([1.0, 0.0, 0.0])  # sim = 1.0
    d = score_posting_decomposed(posting, profile, tier=1, applied_basis=basis)
    assert d.caps["analyst_family_gate"]["fired"] is True
    assert d.applied_corpus_boost["eligible"] is False
    assert d.applied_corpus_boost["eligibility"]["analyst_gate_ok"] is False
    assert d.applied_corpus_boost["boost_points"] == 0
    assert d.final <= ANALYST_GATE_CAP


def test_a3_blindspot_senior_no_boost() -> None:
    """Out-of-target seniority (senior PM) gets NO boost even at perfect sim —
    the blind-spot guard (Range/MeridianLink Senior-style)."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.senior_pm.value,  # NOT in {apm, pm}
        similarity_score=100,
        jd_embedding=[1.0, 0.0, 0.0],
    )
    basis = _basis([1.0, 0.0, 0.0])  # sim = 1.0
    pre = score_posting(posting, profile, tier=1)
    d = score_posting_decomposed(posting, profile, tier=1, applied_basis=basis)
    assert d.applied_corpus_boost["eligibility"]["seniority_in_target"] is False
    assert d.applied_corpus_boost["eligible"] is False
    assert d.applied_corpus_boost["boost_points"] == 0
    assert d.final == pre  # not lifted


def test_a3_upside_eligible_pm_is_boosted() -> None:
    """An eligible in-target PM, high sim, no cap → positive boost, final lifts
    above pre-boost (JPMorgan-PM-style upside)."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        similarity_score=50,  # keep pre-boost < 100 so the lift is visible
        jd_embedding=[1.0, 0.0, 0.0],
    )
    basis = _basis([1.0, 0.0, 0.0], n=16)  # sim = 1.0
    d = score_posting_decomposed(posting, profile, tier=4, applied_basis=basis)
    assert d.applied_corpus_boost["eligible"] is True
    assert d.applied_corpus_boost["boost_points"] > 0
    assert d.final > d.applied_corpus_boost["pre_boost_final"]


def test_a3_never_buries_low_sim() -> None:
    """A high-fit, LOW-sim row (below reference band) is unchanged — boost is
    lift-only and 0 below the band (the no-bury guard)."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        similarity_score=100,
        jd_embedding=[0.0, 1.0, 0.0],  # orthogonal to centroid → sim 0
    )
    basis = _basis([1.0, 0.0, 0.0])
    pre = score_posting(posting, profile, tier=1)
    d = score_posting_decomposed(posting, profile, tier=1, applied_basis=basis)
    assert d.applied_corpus_boost["boost_points"] == 0
    assert d.final == pre  # never lowered


def test_a3_confidence_factor_scales_and_surfaces_n() -> None:
    """f(n)=min(1,n/30) surfaced; a bigger basis yields a bigger boost."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        similarity_score=50,
        jd_embedding=[1.0, 0.0, 0.0],
    )
    d15 = score_posting_decomposed(
        posting, profile, tier=4, applied_basis=_basis([1.0, 0, 0], n=15)
    )
    d30 = score_posting_decomposed(
        posting, profile, tier=4, applied_basis=_basis([1.0, 0, 0], n=30)
    )
    assert d15.applied_corpus_boost["confidence_factor"] == round(min(1.0, 15 / 30), 4)
    assert d30.applied_corpus_boost["confidence_factor"] == 1.0
    assert d30.applied_corpus_boost["n"] == 30
    assert d30.applied_corpus_boost["boost_points"] > d15.applied_corpus_boost["boost_points"]


def test_a3_boost_block_surfaces_eligibility_inputs() -> None:
    """The block exposes seniority_in_target, included_set, seniority_level so
    every boost decision is inspectable."""
    profile = _a3_profile(weight=1.0)
    posting = _make_posting(
        role_family=RoleFamily.product_management.value,
        seniority_level=SeniorityLevel.pm.value,
        jd_embedding=[1.0, 0.0, 0.0],
    )
    d = score_posting_decomposed(posting, profile, tier=1, applied_basis=_basis([1.0, 0, 0]))
    elig = d.applied_corpus_boost["eligibility"]
    assert elig["included_set"] == ["apm", "pm"]
    assert elig["seniority_level"] == "pm"
    assert elig["seniority_in_target"] is True
    assert elig["role_gate_ok"] is True
    assert d.applied_corpus_boost["boost_points"] <= MAX_APPLIED_BOOST
