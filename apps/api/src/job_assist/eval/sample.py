"""Sample-pool counting + stratified selection for the JD eval set.

Pure functions over the ``/postings`` item shape so they're unit-testable with
no network. The hard-seniority heuristics mirror the scorer's intent: titles
that routinely under-level, and the disguised-senior comp rule
(USD floor >= 175k at a junior/unknown seniority bucket).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Titles that routinely carry senior altitude the parser under-levels.
HARD_SENIORITY_TITLE_RE = re.compile(
    r"\b(group|head|director|vp|vice president|chief|principal|staff|lead|sr\.?|senior)\b",
    re.IGNORECASE,
)
# Mirrors scoring._DISGUISED_SENIOR_SALARY_FLOOR_USD / _DISGUISED_SENIOR_SENIORITY.
DISGUISED_COMP_FLOOR_USD = 175_000
DISGUISED_SENIORITY_BUCKETS = frozenset({"pm", "unknown"})


def resolved_bucket(item: dict[str, Any]) -> str:
    """Map a /postings item to applied / passed / triage / other.

    ``state.resolved_status`` drives applied/interview/offer/rejected; a
    ``not_interested`` action with no resolved status is "passed"; nothing is
    "triage".
    """
    state = item.get("state") or {}
    resolved = state.get("resolved_status")
    if resolved in ("applied", "interview", "offer"):
        return "applied"
    if resolved == "rejected":
        return "rejected"
    if state.get("current") == "not_interested":
        return "passed"
    if not state.get("current") or state.get("current") == "reset":
        return "triage"
    return "other"


def _salary_min_usd(item: dict[str, Any]) -> int | None:
    salary = item.get("salary") or {}
    if (salary.get("currency") or "USD") != "USD":
        return None
    return salary.get("min")


def is_hard_seniority(item: dict[str, Any]) -> bool:
    """Title matches a known under-leveling pattern OR disguised-senior comp."""
    role = item.get("role") or {}
    title = role.get("title") or ""
    if HARD_SENIORITY_TITLE_RE.search(title):
        return True
    seniority = role.get("seniority")
    floor = _salary_min_usd(item)
    return bool(
        floor is not None
        and floor >= DISGUISED_COMP_FLOOR_USD
        and seniority in DISGUISED_SENIORITY_BUCKETS
    )


def compute_counts(postings: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the open-posting pool for sample sizing (counts only, no rows)."""
    by_resolved: Counter[str] = Counter()
    by_seniority: Counter[str] = Counter()
    by_family: Counter[str] = Counter()
    hard_seniority = 0
    disguised_comp = 0
    for item in postings:
        by_resolved[resolved_bucket(item)] += 1
        role = item.get("role") or {}
        by_seniority[str(role.get("seniority"))] += 1
        by_family[str(role.get("family"))] += 1
        if is_hard_seniority(item):
            hard_seniority += 1
        floor = _salary_min_usd(item)
        if (
            floor is not None
            and floor >= DISGUISED_COMP_FLOOR_USD
            and role.get("seniority") in DISGUISED_SENIORITY_BUCKETS
        ):
            disguised_comp += 1
    return {
        "total_open": len(postings),
        "by_resolved_status": dict(by_resolved),
        "by_seniority": dict(by_seniority),
        "by_role_family": dict(by_family),
        "hard_seniority_pool": hard_seniority,
        "disguised_comp_pool": disguised_comp,
    }
