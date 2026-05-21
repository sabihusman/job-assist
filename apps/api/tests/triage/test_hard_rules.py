"""Unit tests for the hard-rule filter.

Pure-function, in-memory tests. We instantiate ORM rows directly and hand
them to ``apply_hard_rules`` — no DB session needed.

All company names in fixtures are synthetic (``TestCompany``,
``FakeStaffingFirm``) to keep real targeting data out of the public tree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from job_assist.db.enums import (
    ClosedChannelReason,
    RemoteType,
    RoleFamily,
    SalaryPeriod,
    SeniorityLevel,
)
from job_assist.db.models import ClosedChannel, JobPosting, TargetCompany
from job_assist.triage.config import HardRuleConfig
from job_assist.triage.hard_rules import FilterResult, apply_hard_rules

# ── Builders ──────────────────────────────────────────────────────────────────


def _posting(
    *,
    title: str = "Senior Product Manager",
    location_raw: str | None = "New York, NY",
    locations_normalized: list[dict[str, Any]] | None = None,
    role_family: RoleFamily = RoleFamily.product_management,
    salary_max: int | None = 200_000,
    salary_currency: str | None = "USD",
    salary_period: SalaryPeriod = SalaryPeriod.annual,
    applicant_count: int | None = None,
    canonical_company_name: str = "TestCompany",
) -> JobPosting:
    """A `JobPosting` with sensible defaults that pass every rule."""
    now = datetime.now(tz=UTC)
    return JobPosting(
        canonical_company_name=canonical_company_name,
        normalized_title=title.lower(),
        raw_title=title,
        location_raw=location_raw,
        locations_normalized=locations_normalized,  # type: ignore[arg-type]
        remote_type=RemoteType.onsite,
        salary_min=None,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
        seniority_level=SeniorityLevel.senior_pm,
        role_family=role_family,
        jd_text="x",
        jd_text_hash="0" * 64,
        content_hash="0" * 64,
        first_seen_at=now,
        last_seen_at=now,
        applicant_count=applicant_count,
    )


def _target(
    *,
    name: str = "TestCompany",
    role_filter: str | None = None,
) -> TargetCompany:
    return TargetCompany(name=name, tier=1, ats="unknown", role_filter=role_filter)


def _closed(
    *,
    company_name: str = "TestCompany",
    reason: ClosedChannelReason = ClosedChannelReason.multiple_rejections,
    unsealed: bool = False,
    rejection_count: int = 3,
) -> ClosedChannel:
    cc = ClosedChannel(
        company_name=company_name,
        reason=reason,
        rejection_count=rejection_count,
        closed_at=datetime.now(tz=UTC),
        unsealed_at=datetime.now(tz=UTC) if unsealed else None,
    )
    return cc


# ── Rule-by-rule ──────────────────────────────────────────────────────────────


class TestClosedChannel:
    def test_active_closed_channel_blocks(self) -> None:
        posting = _posting()
        target = _target()
        result = apply_hard_rules(posting, target, _closed())
        assert result.passed is False
        assert result.failed_rule == "closed_channel"
        assert "TestCompany" in result.detail
        assert "rejections=3" in result.detail

    def test_unsealed_closed_channel_does_not_block(self) -> None:
        """A row with unsealed_at IS NOT NULL is no longer active."""
        posting = _posting()
        target = _target()
        result = apply_hard_rules(posting, target, _closed(unsealed=True))
        assert result.passed is True

    def test_no_closed_channel_row_does_not_block(self) -> None:
        result = apply_hard_rules(_posting(), _target(), closed_channel=None)
        assert result.passed is True


class TestRoleFilter:
    def test_non_pm_only_blocks_pm_role(self) -> None:
        posting = _posting(role_family=RoleFamily.product_management)
        target = _target(role_filter="non_pm_only")
        result = apply_hard_rules(posting, target)
        assert result.passed is False
        assert result.failed_rule == "role_filter"

    def test_non_pm_only_blocks_product_owner(self) -> None:
        posting = _posting(role_family=RoleFamily.product_owner)
        target = _target(role_filter="non_pm_only")
        result = apply_hard_rules(posting, target)
        assert result.failed_rule == "role_filter"

    def test_non_pm_only_allows_program_manager(self) -> None:
        """Program management is intentionally NOT in the PM family."""
        posting = _posting(role_family=RoleFamily.program_management)
        target = _target(role_filter="non_pm_only")
        result = apply_hard_rules(posting, target)
        assert result.passed is True

    def test_non_pm_only_allows_other_role(self) -> None:
        posting = _posting(role_family=RoleFamily.other)
        target = _target(role_filter="non_pm_only")
        result = apply_hard_rules(posting, target)
        assert result.passed is True

    def test_no_role_filter_set_does_not_block_pm_role(self) -> None:
        result = apply_hard_rules(_posting(), _target(role_filter=None))
        assert result.passed is True


class TestStaffingFirm:
    @pytest.mark.parametrize(
        "company_name",
        ["Robert Half", "robert half technology", "Aerotek", "Insight Global Inc."],
    )
    def test_blocklist_matches_substring_case_insensitive(self, company_name: str) -> None:
        # canonical_company_name on posting carries the staffing firm.
        result = apply_hard_rules(
            _posting(canonical_company_name=company_name),
            target_company=None,
        )
        assert result.failed_rule == "staffing_firm"

    def test_blocklist_against_target_company_name(self) -> None:
        """Match via target_company.name when canonical name is innocuous."""
        result = apply_hard_rules(
            _posting(canonical_company_name="Unrelated"),
            _target(name="Robert Half Technology"),
        )
        assert result.failed_rule == "staffing_firm"

    def test_clean_company_name_does_not_match(self) -> None:
        result = apply_hard_rules(
            _posting(canonical_company_name="FreshCorp"),
            _target(name="FreshCorp"),
        )
        assert result.passed is True


class TestGeo:
    @pytest.mark.parametrize(
        "location",
        ["Remote", "Remote, US", "Remote — North America", "remote, anywhere"],
    )
    def test_remote_locations_pass(self, location: str) -> None:
        result = apply_hard_rules(_posting(location_raw=location), _target())
        assert result.passed is True

    @pytest.mark.parametrize(
        "location",
        ["New York, NY", "Bay Area", "San Francisco, CA", "Seattle / Remote"],
    )
    def test_whitelisted_cities_pass(self, location: str) -> None:
        result = apply_hard_rules(_posting(location_raw=location), _target())
        assert result.passed is True

    @pytest.mark.parametrize("location", ["Topeka, KS", "Phoenix, AZ", "Pittsburgh, PA"])
    def test_non_whitelist_cities_fail(self, location: str) -> None:
        result = apply_hard_rules(_posting(location_raw=location), _target())
        assert result.failed_rule == "geo_whitelist"

    def test_locations_normalized_also_checked(self) -> None:
        """The whitelist also runs against entries in locations_normalized."""
        posting = _posting(
            location_raw="Phoenix, AZ",  # not whitelisted
            locations_normalized=[{"city": "Austin", "remote_type": "onsite"}],
        )
        result = apply_hard_rules(posting, _target())
        assert result.passed is True

    def test_empty_location_passes(self) -> None:
        """No location string at all — skip the geo rule rather than failing."""
        result = apply_hard_rules(_posting(location_raw=None), _target())
        assert result.passed is True


class TestSalaryFloor:
    def test_under_floor_blocks(self) -> None:
        posting = _posting(salary_max=70_000)
        result = apply_hard_rules(posting, _target())
        assert result.failed_rule == "salary_floor"
        assert "$70,000" in result.detail

    def test_at_floor_passes(self) -> None:
        posting = _posting(salary_max=85_000)
        result = apply_hard_rules(posting, _target())
        assert result.passed is True

    def test_unknown_salary_passes(self) -> None:
        """salary_max=None must NOT be a false negative."""
        posting = _posting(salary_max=None)
        result = apply_hard_rules(posting, _target())
        assert result.passed is True

    def test_non_usd_currency_is_skipped(self) -> None:
        """Don't penalise EUR/GBP rows we haven't converted yet."""
        posting = _posting(salary_max=50_000, salary_currency="EUR")
        result = apply_hard_rules(posting, _target())
        assert result.passed is True

    def test_hourly_salary_is_skipped(self) -> None:
        """Don't false-fail rows whose comp is hourly (50/hr * 2080 = $104k)."""
        posting = _posting(salary_max=50, salary_period=SalaryPeriod.hourly)
        result = apply_hard_rules(posting, _target())
        assert result.passed is True


class TestApplicantCap:
    def test_over_cap_blocks(self) -> None:
        posting = _posting(applicant_count=300)
        result = apply_hard_rules(posting, _target())
        assert result.failed_rule == "applicant_cap"

    def test_at_cap_passes(self) -> None:
        posting = _posting(applicant_count=150)
        result = apply_hard_rules(posting, _target())
        assert result.passed is True

    def test_unknown_count_passes(self) -> None:
        posting = _posting(applicant_count=None)
        result = apply_hard_rules(posting, _target())
        assert result.passed is True


# ── Priority order ────────────────────────────────────────────────────────────


class TestPriority:
    def test_closed_channel_short_circuits_salary_failure(self) -> None:
        """Both closed_channel AND salary fail → cheapest check (closed_channel) wins."""
        posting = _posting(salary_max=50_000)  # would fail salary_floor
        result = apply_hard_rules(posting, _target(), _closed())
        assert result.failed_rule == "closed_channel"

    def test_role_filter_short_circuits_geo_failure(self) -> None:
        """role_filter fires before geo (cheaper attribute lookup)."""
        posting = _posting(
            role_family=RoleFamily.product_management,
            location_raw="Topeka, KS",
        )
        result = apply_hard_rules(posting, _target(role_filter="non_pm_only"))
        assert result.failed_rule == "role_filter"

    def test_staffing_firm_short_circuits_salary_failure(self) -> None:
        posting = _posting(
            canonical_company_name="Robert Half Technology",
            salary_max=50_000,
        )
        result = apply_hard_rules(posting, target_company=None)
        assert result.failed_rule == "staffing_firm"


# ── Clean / passing path ──────────────────────────────────────────────────────


class TestPassingPath:
    def test_clean_posting_passes(self) -> None:
        result = apply_hard_rules(_posting(), _target())
        assert result == FilterResult(passed=True, failed_rule="no_rule_failed", detail="passed")

    def test_passes_with_minimal_target_company(self) -> None:
        result = apply_hard_rules(_posting(), target_company=None)
        assert result.passed is True

    def test_passes_with_no_config_supplied(self) -> None:
        """Default HardRuleConfig is applied when caller omits config."""
        result = apply_hard_rules(_posting(), _target(), None, None)
        assert result.passed is True


# ── Defaults sanity ───────────────────────────────────────────────────────────


class TestDefaults:
    def test_defaults_match_documented_thresholds(self) -> None:
        """Catch accidental drift between the dataclass and DECISIONS.md."""
        cfg = HardRuleConfig()
        assert cfg.salary_floor_usd == 85_000
        assert cfg.applicant_cap == 150
        # Whitelist contains all the cities the operator currently considers.
        for expected_city in (
            "Remote",
            "New York",
            "Austin",
            "San Francisco",
            "Seattle",
            "Chicago",
        ):
            assert expected_city in cfg.geo_whitelist
        # Blocklist covers the major US staffing firms.
        for expected_firm in (
            "Robert Half",
            "Aerotek",
            "Insight Global",
            "TEKsystems",
            "Randstad",
        ):
            assert expected_firm in cfg.staffing_firm_blocklist


# ── Config tunability ─────────────────────────────────────────────────────────


def test_custom_config_overrides_defaults() -> None:
    """Tighter floor catches a posting that would pass under the default."""
    posting = _posting(salary_max=100_000)  # passes default floor (85k)
    cfg = HardRuleConfig(salary_floor_usd=120_000)
    result = apply_hard_rules(posting, _target(), None, cfg)
    assert result.failed_rule == "salary_floor"


# ── PR #43: Salary ceiling ────────────────────────────────────────────────────


class TestSalaryCeiling:
    """Symmetric to the floor rule. ``salary_min`` is the comparison key."""

    def test_drops_posting_above_ceiling(self) -> None:
        posting = _posting(salary_max=300_000)
        posting.salary_min = 250_000
        cfg = HardRuleConfig(salary_ceiling_usd=180_000)
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is False
        assert result.failed_rule == "salary_ceiling"

    def test_allows_posting_at_ceiling(self) -> None:
        posting = _posting()
        posting.salary_min = 180_000
        cfg = HardRuleConfig(salary_ceiling_usd=180_000)
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True

    def test_allows_posting_below_ceiling(self) -> None:
        posting = _posting()
        posting.salary_min = 120_000
        cfg = HardRuleConfig(salary_ceiling_usd=180_000)
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True

    def test_allows_posting_with_null_salary_min(self) -> None:
        """Unknown comp → surface for triage rather than silent drop."""
        posting = _posting()
        posting.salary_min = None
        cfg = HardRuleConfig(salary_ceiling_usd=180_000)
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True

    def test_disabled_when_ceiling_is_none(self) -> None:
        """``salary_ceiling_usd=None`` short-circuits — no rule evaluated."""
        posting = _posting()
        posting.salary_min = 999_999  # would fail any positive ceiling
        cfg = HardRuleConfig(salary_ceiling_usd=None)
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True


# ── PR #43: Seniority levels ──────────────────────────────────────────────────


class TestSeniorityLevels:
    def test_drops_posting_outside_included_set(self) -> None:
        posting = _posting()
        posting.seniority_level = SeniorityLevel.principal_pm
        cfg = HardRuleConfig(seniority_levels_included=("apm", "pm"))
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is False
        assert result.failed_rule == "seniority_levels"

    def test_keeps_posting_inside_included_set(self) -> None:
        posting = _posting()
        posting.seniority_level = SeniorityLevel.pm
        cfg = HardRuleConfig(seniority_levels_included=("apm", "pm"))
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True

    def test_allows_unknown_seniority_when_filter_active(self) -> None:
        """``unknown`` seniority passes through — surface for triage."""
        posting = _posting()
        posting.seniority_level = SeniorityLevel.unknown
        cfg = HardRuleConfig(seniority_levels_included=("apm", "pm"))
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True

    def test_empty_tuple_disables_filter(self) -> None:
        posting = _posting()
        posting.seniority_level = SeniorityLevel.principal_pm
        cfg = HardRuleConfig(seniority_levels_included=())
        result = apply_hard_rules(posting, _target(), None, cfg)
        assert result.passed is True
