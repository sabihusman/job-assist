"""Hard-rule filter — layer 1 of the three-layer triage pipeline.

The function is pure: it takes the rows it needs and returns a structured
verdict. No DB writes, no LLM calls, no embedding calls. The caller (the
future triage cron) fetches the inputs and decides what to do with the
result — typically: drop the posting from the triage queue, log the
failed rule, and never spend an embedding or LLM budget on it.

Rule priority (cheapest first, short-circuits at first failure):

  1. closed_channel    — operator has flagged this company as off-limits
  2. role_filter       — company has 'non_pm_only' and posting is PM-family
  3. staffing_firm     — canonical_company_name is in the staffing blocklist
  4. company_blocklist — canonical_company_name is in the employer blocklist
  5. geo_whitelist     — location doesn't intersect the whitelist. For
     business_analyst/financial_analyst rows this rule is REPLACED (not
     stacked) by the analyst geo rule — see ``_analyst_geo_passes`` — same
     chain position, branched on role_family. Failure reason is
     'analyst_geo' instead of 'geo_whitelist' for those rows.
  6. salary_floor      — annual USD max < floor (tolerates unknown salary)
  7. applicant_cap     — public applicant count > cap (tolerates unknown)

Deviation from PR #23's literal spec
────────────────────────────────────
The spec showed ``apply_hard_rules`` reading ``target_company.is_closed_channel``
and ``target_company.closed_reason`` directly. Those columns don't exist on
the schema — closed-channel state lives in its own table (``closed_channel``)
with a ``unsealed_at IS NULL`` flag denoting "currently sealed". To avoid
denormalising that state onto ``target_company`` (where it would inevitably
drift out of sync with the source-of-truth table), this function takes the
already-fetched ``ClosedChannel | None`` row as a parameter. The caller
(the future triage cron) does:

    closed = await session.execute(
        select(ClosedChannel)
        .where(ClosedChannel.target_company_id == tc.id)
        .where(ClosedChannel.unsealed_at.is_(None))
    ).scalar_one_or_none()
    result = apply_hard_rules(posting, target_company, closed, config)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from job_assist.db.enums import RemoteType, RoleFamily, SalaryPeriod
from job_assist.db.models import ClosedChannel, JobPosting, TargetCompany
from job_assist.triage.config import HardRuleConfig

RuleName = Literal[
    "closed_channel",
    "role_filter",
    "staffing_firm",
    # feat/company-blocklist: parallel employer blocklist (normalized match).
    "company_blocklist",
    "geo_whitelist",
    # business_analyst/financial_analyst geo expansion: analyst-family rows
    # branch to this reason instead of "geo_whitelist" — see the family
    # branch at the geo-whitelist site in ``apply_hard_rules``.
    "analyst_geo",
    "salary_floor",
    # PR #43: paired with salary_floor; uses ``salary_min`` rather than max.
    "salary_ceiling",
    "applicant_cap",
    # PR #43: explicit set of allowed seniority levels.
    "seniority_levels",
    "no_rule_failed",
]


@dataclass(frozen=True)
class FilterResult:
    """Outcome of applying the hard-rule chain to a single posting."""

    passed: bool
    failed_rule: RuleName  # "no_rule_failed" when ``passed`` is True
    detail: str  # human-readable rationale for logs / digest


# Role families considered "PM" for the ``non_pm_only`` rule. Product
# marketing and program management are deliberately NOT in this set — they
# are different functions and a "non_pm_only" company is happy to consider
# them.
_PM_FAMILIES: frozenset[RoleFamily] = frozenset(
    {RoleFamily.product_management, RoleFamily.product_owner}
)

# business_analyst/financial_analyst geo expansion: these two families replace
# the standard geo_whitelist rule with the analyst geo rule below (does NOT
# stack — see ``apply_hard_rules`` step 5). Mirrors ``_PM_FAMILIES`` — a local
# constant rather than importing ``scoring.ANALYST_FAMILIES`` so triage/ stays
# free of a services/ dependency (same reasoning ``_PM_FAMILIES`` follows).
_ANALYST_FAMILIES: frozenset[RoleFamily] = frozenset(
    {RoleFamily.business_analyst, RoleFamily.financial_analyst}
)

# Des Moines metro allowlist for the analyst geo rule. Case-insensitive
# substring match against both ``location_raw`` and every
# ``locations_normalized`` entry (via ``_collect_location_strings`` +
# ``_geo_matches``, reused as-is — belt-and-suspenders since some adapters
# don't populate ``locations_normalized`` cleanly).
DES_MOINES_METRO: frozenset[str] = frozenset(
    {
        "des moines",
        "west des moines",
        "ankeny",
        "urbandale",
        "waukee",
        "clive",
        "johnston",
        "altoona",
    }
)


def _collect_location_strings(posting: JobPosting) -> list[str]:
    """All location strings worth scanning for the geo-whitelist check."""
    out: list[str] = []
    if posting.location_raw:
        out.append(posting.location_raw)
    raw = posting.locations_normalized
    # locations_normalized is JSONB — we serialise it as a list[dict[str,Any]]
    # in the adapters but the mapped type is dict | None for schema reasons.
    # Iterate defensively.
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                for key in ("city", "region", "country", "raw"):
                    val = entry.get(key)
                    if isinstance(val, str) and val:
                        out.append(val)
    return out


def _remote_kind(location_raw: str | None) -> str:
    """Classify a raw location string's remote-ness for the geo gate.

    Returns one of:
      * ``"us_remote"``    — contains a remote keyword AND, after stripping the
        keyword + US synonyms (US / U.S. / USA / United States) + non-US
        filler ("fully", "100%", "position", …) + punctuation, NOTHING
        geographic remains. These should PASS geo regardless of whitelist
        (the operator wants US/unspecified-remote roles surfaced).
      * ``"non_us_remote"`` — remote keyword present BUT a region qualifier
        survives ("Remote - India", "EMEA Remote", "Remote (Canada)", and
        ambiguous "North America"/"Americas"/"Anywhere"). These must NOT get a
        free pass — they fall through to the normal whitelist match (and fail
        unless a whitelisted city/state is also present, e.g. a US state).
      * ``"not_remote"``   — no remote keyword; ordinary whitelist matching.

    Dots are stripped first so "U.S." == "US". State/city-qualified US-remote
    (e.g. "Remote - California") is intentionally NOT us_remote here — it still
    passes via ``_geo_matches`` since the whitelist carries those states.
    """
    if not location_raw:
        return "not_remote"
    text = location_raw.lower().replace(".", "")
    if not re.search(r"\bremote\b", text):
        return "not_remote"
    text = re.sub(r"\bremote\b", " ", text)
    text = re.sub(r"\b(united states|usa|us)\b", " ", text)  # US synonyms (dots already gone)
    text = re.sub(r"\b(fully|100|percent|position|role|based|only|wfh)\b", " ", text)
    leftover = re.sub(r"[^a-z]", "", text)  # keep only letters
    return "us_remote" if not leftover else "non_us_remote"


def _geo_matches(locations: list[str], whitelist: tuple[str, ...]) -> bool:
    """Case-insensitive substring match across any (location, whitelist) pair."""
    if not locations:
        return False
    lower_locs = [loc.lower() for loc in locations]
    for allowed in whitelist:
        needle = allowed.lower()
        if any(needle in loc for loc in lower_locs):
            return True
    return False


def _analyst_geo_passes(posting: JobPosting) -> bool:
    """Analyst-family (business_analyst/financial_analyst) geo rule.

    Replaces ``geo_whitelist`` for these two families (does not stack — see
    the family branch in ``apply_hard_rules``). Passes iff:

      (a) ``remote_type == RemoteType.remote`` AND ``_remote_kind`` on
          ``location_raw`` is not ``"non_us_remote"`` — i.e. a region-
          qualified non-US remote ("Remote - India") does NOT get the free
          pass a plain/unspecified/US remote does; OR
      (b) any collected location string (``location_raw`` plus every
          ``locations_normalized`` entry, via ``_collect_location_strings``)
          case-insensitively matches an entry in ``DES_MOINES_METRO`` — any-
          match semantics via ``_geo_matches``, reused unchanged.
    """
    remote_pass = (
        posting.remote_type == RemoteType.remote
        and _remote_kind(posting.location_raw) != "non_us_remote"
    )
    if remote_pass:
        return True
    locations = _collect_location_strings(posting)
    return _geo_matches(locations, tuple(DES_MOINES_METRO))


def _matches_staffing_firm(name: str, blocklist: tuple[str, ...]) -> bool:
    name_lower = name.lower()
    return any(firm.lower() in name_lower for firm in blocklist)


def _normalize_company(name: str) -> str:
    """Lowercase and strip every non-alphanumeric char (punctuation, spaces,
    ``&``, ``-``, ``.``) so company-name variants collapse to one comparable
    form: ``"J.P. Morgan"`` → ``"jpmorgan"``, ``"JPMorgan - XML"`` →
    ``"jpmorganxml"``, ``"JPMorgan Chase & Co."`` → ``"jpmorganchaseco"``."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _matches_company_blocklist(name: str, blocklist: tuple[str, ...]) -> bool:
    """Normalized substring match, aligned to word boundaries of ``name``.

    Each blocklist entry is a full company name (never a bare token like
    "Chase"). The entry is collapsed via :func:`_normalize_company` so surface
    variants match ("J.P. Morgan" ⊇ entry "JPMorgan"), but a match must start
    AND end on a word boundary of the original name — otherwise short collapsed
    entries bleed across words: entry "BofA" → "bofa" would substring-match
    "Lab of America" → "labofamerica". Boundary alignment rejects that (the
    match starts mid-word) while keeping "BofA Securities" → ["bofa",
    "securities"] a hit.
    """
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    if not tokens:
        return False
    norm_name = "".join(tokens)
    # Offsets in norm_name where a word of the original name starts/ends.
    boundaries = {0}
    pos = 0
    for token in tokens:
        pos += len(token)
        boundaries.add(pos)
    for entry in blocklist:
        norm_entry = _normalize_company(entry)
        if not norm_entry:
            continue
        start = norm_name.find(norm_entry)
        while start != -1:
            if start in boundaries and (start + len(norm_entry)) in boundaries:
                return True
            start = norm_name.find(norm_entry, start + 1)
    return False


def _period_label(period: Any) -> str:
    """Render salary_period for the detail string, tolerating BOTH the
    SalaryPeriod enum (DB-loaded path) and a raw str (ingest-time path, where
    the in-memory posting carries ``norm.salary_period`` as a plain string
    before any flush/coercion). ``.value`` only exists on the enum — calling
    it on a str raises AttributeError and silently loses the failed rule."""
    return str(getattr(period, "value", period))


def _under_salary_floor(posting: JobPosting, floor_usd: int) -> bool:
    """True when the posting's annual USD max is known and below the floor.

    Returns False for unknown salary, hourly comp, or non-USD currency — we
    don't penalise rows where the salary data isn't comparable.
    """
    if posting.salary_max is None:
        return False
    if posting.salary_period not in (SalaryPeriod.annual, SalaryPeriod.unknown):
        return False
    # Treat USD or unset currency as USD (legacy rows). Anything else: skip.
    currency = posting.salary_currency
    if currency is not None and currency.upper() != "USD":
        return False
    return posting.salary_max < floor_usd


def _over_salary_ceiling(posting: JobPosting, ceiling_usd: int) -> bool:
    """True when the posting's annual USD min is known and above the ceiling.

    Symmetric to ``_under_salary_floor`` but uses ``salary_min`` so a
    posting advertising "$200k-$280k" gets dropped when the ceiling is
    $180k. Unknown comp / non-USD / non-annual rows pass through —
    same tolerance as the floor rule.
    """
    if posting.salary_min is None:
        return False
    if posting.salary_period not in (SalaryPeriod.annual, SalaryPeriod.unknown):
        return False
    currency = posting.salary_currency
    if currency is not None and currency.upper() != "USD":
        return False
    return posting.salary_min > ceiling_usd


def apply_hard_rules(
    posting: JobPosting,
    target_company: TargetCompany | None,
    closed_channel: ClosedChannel | None = None,
    config: HardRuleConfig | None = None,
) -> FilterResult:
    """Apply the six hard rules in priority order. Pure function.

    ``closed_channel`` should be the active (``unsealed_at IS NULL``) row
    for ``target_company`` if any; pass ``None`` when none exists.
    """
    cfg = config or HardRuleConfig()

    # 1. Closed channel — operator-flagged company.
    if closed_channel is not None and closed_channel.unsealed_at is None:
        company_name = (
            target_company.name if target_company is not None else closed_channel.company_name
        )
        return FilterResult(
            passed=False,
            failed_rule="closed_channel",
            detail=(
                f"{company_name} is a closed channel "
                f"(reason={closed_channel.reason}, rejections={closed_channel.rejection_count})"
            ),
        )

    # 2. Role filter — company configured to only accept non-PM roles.
    if (
        target_company is not None
        and target_company.role_filter == "non_pm_only"
        and posting.role_family in _PM_FAMILIES
    ):
        return FilterResult(
            passed=False,
            failed_rule="role_filter",
            detail=(
                f"{target_company.name} has role_filter='non_pm_only' "
                f"and posting role_family={posting.role_family.value} is PM-family"
            ),
        )

    # 3. Staffing firm — match against canonical_company_name and the
    #    target_company.name when present.
    candidate_names: list[str] = [posting.canonical_company_name]
    if target_company is not None:
        candidate_names.append(target_company.name)
    for name in candidate_names:
        if _matches_staffing_firm(name, cfg.staffing_firm_blocklist):
            return FilterResult(
                passed=False,
                failed_rule="staffing_firm",
                detail=f"'{name}' matches the staffing-firm blocklist",
            )

    # 4. Company blocklist — operator-flagged dead-end employers. Same
    #    candidate names as the staffing rule, but matched on the NORMALIZED
    #    (alphanumeric-only) form so name variants collapse: "J.P. Morgan",
    #    "JPMorgan - XML", "JPMorgan Chase & Co." all hit a "JPMorgan" entry.
    for name in candidate_names:
        if _matches_company_blocklist(name, cfg.company_blocklist):
            return FilterResult(
                passed=False,
                failed_rule="company_blocklist",
                detail=f"'{name}' matches the company blocklist",
            )

    # 5. Geo whitelist — OR, for business_analyst/financial_analyst rows, the
    #    analyst geo rule in its place (does not stack; see _analyst_geo_passes).
    #    US/unspecified-remote roles pass the PM/PO/other path regardless of
    #    the whitelist (the operator wants them surfaced even though "Remote"
    #    isn't a whitelist entry); region-qualified non-US remote
    #    ("Remote - India") gets NO free pass and must still satisfy the
    #    whitelist (so it fails).
    if posting.role_family in _ANALYST_FAMILIES:
        if not _analyst_geo_passes(posting):
            return FilterResult(
                passed=False,
                failed_rule="analyst_geo",
                detail=(
                    f"analyst-family posting (role_family={posting.role_family!s}) is not "
                    f"US-remote and location {_collect_location_strings(posting)!r} is "
                    "outside the Des Moines metro"
                ),
            )
    else:
        location_strings = _collect_location_strings(posting)
        if (
            location_strings
            and _remote_kind(posting.location_raw) != "us_remote"
            and not _geo_matches(location_strings, cfg.geo_whitelist)
        ):
            return FilterResult(
                passed=False,
                failed_rule="geo_whitelist",
                detail=f"location {location_strings!r} not in geo whitelist",
            )

    # 6. Salary floor.
    if _under_salary_floor(posting, cfg.salary_floor_usd):
        return FilterResult(
            passed=False,
            failed_rule="salary_floor",
            detail=(
                f"salary_max=${posting.salary_max:,} ({posting.salary_currency or 'USD'}, "
                f"{_period_label(posting.salary_period)}) < floor=${cfg.salary_floor_usd:,}"
            ),
        )

    # 7. Salary ceiling (PR #43). Paired with the floor — operator can now
    #    set a range. Skipped when ``salary_ceiling_usd`` is None.
    if cfg.salary_ceiling_usd is not None and _over_salary_ceiling(posting, cfg.salary_ceiling_usd):
        return FilterResult(
            passed=False,
            failed_rule="salary_ceiling",
            detail=(
                f"salary_min=${posting.salary_min:,} ({posting.salary_currency or 'USD'}, "
                f"{_period_label(posting.salary_period)}) > ceiling=${cfg.salary_ceiling_usd:,}"
            ),
        )

    # 8. Applicant cap — tolerated when unknown.
    if posting.applicant_count is not None and posting.applicant_count > cfg.applicant_cap:
        return FilterResult(
            passed=False,
            failed_rule="applicant_cap",
            detail=(f"applicant_count={posting.applicant_count} > cap={cfg.applicant_cap}"),
        )

    # 9. Seniority levels (PR #43). Only applies when the operator has
    #    populated the allowed set; empty/None tuple = filter disabled.
    #    Postings with ``unknown`` / NULL seniority pass through — we'd
    #    rather surface a possibly-mismatched role than silently drop
    #    on missing data.
    if cfg.seniority_levels_included:
        posting_level = posting.seniority_level.value if posting.seniority_level else None
        if (
            posting_level is not None
            and posting_level != "unknown"
            and posting_level not in cfg.seniority_levels_included
        ):
            return FilterResult(
                passed=False,
                failed_rule="seniority_levels",
                detail=(
                    f"seniority_level={posting_level!r} not in {cfg.seniority_levels_included!r}"
                ),
            )

    return FilterResult(passed=True, failed_rule="no_rule_failed", detail="passed")


__all__: list[Any] = ["FilterResult", "RuleName", "apply_hard_rules"]
