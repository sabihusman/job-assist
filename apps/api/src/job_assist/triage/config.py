"""Default thresholds for the hard-rule filter.

These live here for now so the rule code stays free of magic numbers. They
will migrate to an ``operator_profile`` table in PR #29 — at which point
``apply_hard_rules`` will receive an ``OperatorProfile`` row in place of
this dataclass and the defaults below become the seed values for that row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_assist.db.models.operator_profile import OperatorProfile


@dataclass(frozen=True)
class HardRuleConfig:
    """Operator-tunable thresholds applied by ``apply_hard_rules``.

    Frozen so each instance is hashable and safe to reuse across requests.
    """

    # Salary floor in USD/year. Postings whose ``salary_max`` is below this
    # value (when both currency=USD and period=annual) are dropped. ``None``
    # values on the posting are tolerated.
    salary_floor_usd: int = 85_000

    # PR #43: optional upper bound in USD/year. ``None`` = no ceiling (rule
    # disabled). When set, postings whose ``salary_min`` exceeds the
    # ceiling are dropped; postings with NULL ``salary_min`` (unknown
    # comp) pass through so they still reach triage.
    salary_ceiling_usd: int | None = None

    # PR #43: explicit list of ``SeniorityLevel`` enum values to include.
    # Empty tuple = include all (rule disabled). The rule drops postings
    # whose ``seniority_level`` is set and NOT in the tuple; postings with
    # NULL / ``unknown`` seniority pass through.
    seniority_levels_included: tuple[str, ...] = ()

    # Geographic whitelist matched case-insensitively against the posting's
    # ``location_raw`` and the ``locations_normalized`` entries. Any single
    # hit anywhere in the location string passes the rule.
    geo_whitelist: tuple[str, ...] = field(
        default=(
            "Remote",
            "Des Moines",
            "NYC",
            "New York",
            "Austin",
            "San Francisco",
            "Bay Area",
            "Seattle",
            "Minneapolis",
            "Chicago",
        )
    )

    # Postings with more than this many declared applicants are dropped.
    # No ATS adapter populates ``applicant_count`` today; the rule is a
    # no-op for those rows. Raised from 150 → 500 in May 2026 ahead of
    # the LinkedIn adapter — competitive enterprise PM roles regularly
    # show 200-800 applicants, so 150 would have surfaced as a
    # near-universal drop on day one of LinkedIn ingestion. See
    # ``DECISIONS.md`` ADR-008 for the full history note.
    applicant_cap: int = 500

    # Substring blocklist for staffing-firm / agency canonical company
    # names. Match is case-insensitive substring against
    # ``target_company.name`` and the posting's ``canonical_company_name``.
    staffing_firm_blocklist: tuple[str, ...] = field(
        default=(
            "Robert Half",
            "Aerotek",
            "Insight Global",
            "Apex Systems",
            "Beacon Hill",
            "TEKsystems",
            "Modis",
            "Randstad",
            "Kforce",
            "Adecco",
        )
    )


def hard_rule_config_from_profile(profile: OperatorProfile) -> HardRuleConfig:
    """Build a :class:`HardRuleConfig` from the singleton OperatorProfile row.

    The two models hold the same operator-tunable knobs; this bridges the
    persisted JSONB ``list[str]`` columns to the frozen-dataclass ``tuple``
    fields ``apply_hard_rules`` expects. ``seniority_levels_included`` is
    NULL-able on the profile (NULL = filter disabled) and maps to an empty
    tuple. Fields map 1:1; no defaults are invented here — an unseeded knob
    on the profile already carries its own DB default.
    """
    return HardRuleConfig(
        salary_floor_usd=profile.salary_floor_usd,
        salary_ceiling_usd=profile.salary_ceiling_usd,
        seniority_levels_included=tuple(profile.seniority_levels_included or ()),
        geo_whitelist=tuple(profile.geo_whitelist or ()),
        applicant_cap=profile.applicant_cap,
        staffing_firm_blocklist=tuple(profile.staffing_firm_blocklist or ()),
    )
