"""Unit tests for ``adapters/title_filter.py`` (feat/ingest-title-prefilter).

Three blocks pin the filter contract:

  1. Positive keep — PM cluster titles pass.
  2. Negative drop — non-PM titles fail.
  3. Adjacency / context guards — the carve-outs that distinguish
     ``product manager`` from ``product marketing manager``,
     ``product designer``, ``product engineer``, and bare ``pm`` ↔
     ``program manager``.

The third block is the load-bearing one — the filter would be useless
if it kept ``Product Designer`` or rejected ``Senior Product Manager``.
A regression in any of those rows would silently flood Slice 2's
broad-ingest path with non-PM rows OR silently drop real PM rows.
"""

from __future__ import annotations

import pytest

from job_assist.adapters.title_filter import should_keep_title

# ── (1) Positive: PM cluster — these MUST keep ──────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        # Plain forms
        "Product Manager",
        "Product Owner",
        "Product Management",
        "Product Lead",
        # Seniority prefixes
        "Senior Product Manager",
        "Sr. Product Manager",
        "Principal Product Manager",
        "Staff Product Manager",
        "Associate Product Manager",
        "Group Product Manager",
        "Lead Product Manager",
        # Director / VP / Chief
        "Director of Product Management",
        "Director, Product",
        "VP of Product",
        "Head of Product",
        "Chief Product Officer",
        # With suffixes
        "Senior Product Manager, Growth",
        "Senior Product Manager | Payments",
        "Product Manager — Platform",
        "Product Manager (Remote)",
        # Abbreviations
        "APM, Payments",
        "GPM — Growth",
        "Senior PM, Growth (Product Strategy)",  # bare PM with 'product' context
        # Adjacent qualifiers that still read as PM
        "Technical Product Manager",
        "Product Operations Manager",
    ],
)
def test_keep_pm_titles(title: str) -> None:
    assert should_keep_title(title), f"Filter wrongly dropped PM title: {title!r}"


# ── (2) Negative: non-PM titles — these MUST drop ───────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        # Software / data / sales / ops / legal / support
        "Senior Software Engineer",
        "Staff Software Engineer",
        "Data Scientist, Marketing",
        "Director of Sales",
        "Account Executive",
        "Customer Support Specialist",
        "IP Counsel, Copyright",
        "Recruiter, Engineering",
        "Member of Staff, AI & Rule of Law",
        "Engineering Manager",
        "Sales Development Representative",
        # Real corpus examples from production sampling
        "Enterprise Account Executive — Tech, Media, Telco",
        "Head of Enterprise Sales, Industries",
        "Senior People Business Partner",
        "Finance & Strategy, EMEA GTM",
        # Bare PM without product context — drops
        "PM, Operations",
        "Senior PM, Field Service",
        # Empty / whitespace
        "",
        "   ",
    ],
)
def test_drop_non_pm_titles(title: str) -> None:
    assert not should_keep_title(title), f"Filter wrongly kept non-PM title: {title!r}"


# ── (3) Adjacency / context — the load-bearing carve-outs ───────────────────


@pytest.mark.parametrize(
    "title",
    [
        # PMM family — distinct role
        "Product Marketing Manager",
        "Senior Product Marketing Manager",
        "Director of Product Marketing",
        "Growth Marketing Campaigns Manager",  # marketing-flavored but no PM
        # Design family — distinct role
        "Product Designer",
        "Senior Product Designer",
        "Product Design Lead",
        # Engineering family — distinct role
        "Product Engineer",
        "Product Engineering Manager",
        "Senior Product Engineer",
        # Support / analyst / specialist — distinct roles
        "Product Support Specialist",
        "Product Analyst",
        "Product Specialist",
        # Program manager — common bare-PM false positive
        "Program Manager, AI Transformation",
        "Senior Program Manager",
        # Project manager — same
        "Project Manager, Construction",
    ],
)
def test_drop_adjacent_distinct_role_families(title: str) -> None:
    """The filter is conservative on the KEEP side but MUST reject the
    explicit-exclusion list — ``product marketing``, ``product
    designer``, ``product engineer``, ``product analyst``, ``product
    support``. These are distinct role families even though they share
    the ``product`` keyword."""
    assert not should_keep_title(title), (
        f"Filter wrongly kept adjacent-but-distinct role: {title!r}"
    )


def test_none_input_returns_false() -> None:
    """Defensive: a None / NULL title falls through to False rather
    than raising. Adapter-side peek_title methods return ``""`` on a
    missing key so this is belt + braces."""
    assert not should_keep_title(None)  # type: ignore[arg-type]


def test_case_insensitivity() -> None:
    """Real ATS payloads use any case (Greenhouse: Title Case,
    Workday: SCREAMING SOMETIMES). Filter must not depend on case."""
    assert should_keep_title("SENIOR PRODUCT MANAGER")
    assert should_keep_title("senior product manager")
    assert should_keep_title("Senior Product Manager")
    assert not should_keep_title("SENIOR PRODUCT MARKETING MANAGER")


# ── feat/strategy-spine: per-track keep-lists ─────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Strategy & Operations Manager",
        "Senior Strategy and Operations Manager",
        "Strategy Manager",
        "Manager, Corporate Strategy",
        "Business Strategy Lead",
        "Strategy Consultant",
        "Business Operations Manager",
        "BizOps Associate",
        "Biz Ops Lead",
        "Chief of Staff",
        "Chief of Staff to the CEO",
        "Strategy & Planning Manager",
    ],
)
def test_strategy_track_keeps_strategy_family(title: str) -> None:
    assert should_keep_title(title, track="strategy") is True


@pytest.mark.parametrize(
    "title",
    [
        "Strategy & Operations Manager",
        "Chief of Staff",
        "Corporate Strategy Manager",
        "BizOps Associate",
    ],
)
def test_pm_track_still_drops_strategy_family(title: str) -> None:
    """The default (pm) track is byte-identical — strategy titles stay out of
    the on-domain pipeline."""
    assert should_keep_title(title) is False
    assert should_keep_title(title, track="pm") is False


@pytest.mark.parametrize(
    "title",
    ["Product Manager", "Senior Product Owner", "Associate Product Manager"],
)
def test_strategy_track_also_keeps_pm_po(title: str) -> None:
    """strategy track = PM/PO keep-list PLUS the strategy family."""
    assert should_keep_title(title, track="strategy") is True


@pytest.mark.parametrize(
    "title",
    [
        "Operations Manager",
        "Plant Operations Manager",
        "Warehouse Operations Supervisor",
        "IT Project Manager",
        "Software Engineer",
        "Network Operations Engineer",
    ],
)
def test_strategy_track_still_drops_generic_ops(title: str) -> None:
    """Bare operations / delivery titles don't ride the strategy keep-list."""
    assert should_keep_title(title, track="strategy") is False


def test_strategy_track_ignores_pm_exclusions_for_strategy_titles() -> None:
    """A strategy title is kept even if it brushes a product-flavored
    exclusion phrase (the strategy keep-list is checked first)."""
    assert should_keep_title("Strategy & Operations Manager, Product Marketing", track="strategy")


# ── business_analyst/financial_analyst keep-list (not track-gated) ──────────


@pytest.mark.parametrize(
    "title",
    [
        # The four titles from the verification report.
        "Business Analyst",
        "Financial Analyst",
        "FP&A Analyst",
        "Data Analyst",
        # Additional analyst-family variants the regex must also cover.
        "Senior Financial Analyst, Corporate FP&A",
        "BI Analyst",
        "Business Intelligence Analyst",
        "Business Systems Analyst",
        "Operations Analyst",
        "Ops Analyst, Growth",
        "Finance Analyst",
        # Was pinned as a strategy-track drop pre-expansion (it doesn't match
        # _STRATEGY_ROLE_RE) — now correctly survives via the analyst
        # keep-list instead, since the v7 classifier routes it to
        # business_analyst (per the classifier boundary).
        "Sales Operations Analyst",
    ],
)
def test_analyst_titles_survive_pm_track(title: str) -> None:
    """The analyst keep-list is NOT track-gated — it applies on the default
    (pm) track too, so broad-ingest (which never passes track=) still keeps
    these titles instead of dropping them pre-DB."""
    assert should_keep_title(title) is True
    assert should_keep_title(title, track="pm") is True


@pytest.mark.parametrize(
    "title",
    ["Business Analyst", "Financial Analyst", "FP&A Analyst", "Data Analyst"],
)
def test_analyst_titles_also_survive_strategy_track(title: str) -> None:
    assert should_keep_title(title, track="strategy") is True


@pytest.mark.parametrize(
    "title",
    [
        # Deliberate exception: "Product Analyst" must NOT be caught by the
        # new analyst keep-list — it stays dropped, unchanged from v6.
        "Product Analyst",
        "Senior Product Analyst",
        # Titles that share a word with the analyst regex but aren't analyst
        # roles — must still drop.
        "Business Development Representative",
        "Data Engineer",
        "Financial Advisor",
    ],
)
def test_titles_still_dropped_despite_analyst_keep_list(title: str) -> None:
    assert should_keep_title(title) is False
