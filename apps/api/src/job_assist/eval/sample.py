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


def is_hard_seniority_mismatch(item: dict[str, Any]) -> bool:
    """Senior-marker title BUT parsed pm/unknown — the under-leveling signal."""
    role = item.get("role") or {}
    title = role.get("title") or ""
    return bool(
        HARD_SENIORITY_TITLE_RE.search(title)
        and role.get("seniority") in DISGUISED_SENIORITY_BUCKETS
    )


def is_disguised_comp(item: dict[str, Any]) -> bool:
    role = item.get("role") or {}
    floor = _salary_min_usd(item)
    return bool(
        floor is not None
        and floor >= DISGUISED_COMP_FLOOR_USD
        and role.get("seniority") in DISGUISED_SENIORITY_BUCKETS
    )


# Approved JD strata sizes (priority order — a row is assigned its FIRST match).
JD_STRATA_SIZES: list[tuple[str, int]] = [
    ("applied", 17),
    ("hard_seniority_mismatch", 20),
    ("disguised_comp", 13),
    ("triage", 25),
    ("passed", 15),
]

# Approved email strata sizes (by outcome_type / stage).
EMAIL_STRATA_SIZES: dict[str, int] = {
    "application_confirmation": 12,
    "rejection_post_screen": 12,
    "rejection_pre_screen": 10,
    "rejection_post_interview": 5,
    "recruiter_screen_invite": 10,
    "phone_interview_invite": 2,
    "onsite_interview_invite": 1,
    "unclassified": 5,
    "unrelated": 9,
}


def select_jd_sample(postings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic stratified JD selection. Each posting appears once, tagged
    with the first stratum it satisfies (priority order in ``JD_STRATA_SIZES``).
    """
    by_id = {str(p.get("id")): p for p in postings}
    ordered_ids = sorted(by_id)  # deterministic
    chosen: dict[str, str] = {}  # id -> stratum

    def _matches(item: dict[str, Any], stratum: str) -> bool:
        if stratum == "hard_seniority_mismatch":
            return is_hard_seniority_mismatch(item)
        if stratum == "disguised_comp":
            return is_disguised_comp(item)
        return resolved_bucket(item) == stratum

    for stratum, size in JD_STRATA_SIZES:
        taken = 0
        for pid in ordered_ids:
            if taken >= size:
                break
            if pid in chosen:
                continue
            if _matches(by_id[pid], stratum):
                chosen[pid] = stratum
                taken += 1

    out: list[dict[str, Any]] = []
    for pid in ordered_ids:
        if pid in chosen:
            item = dict(by_id[pid])
            item["_stratum"] = chosen[pid]
            out.append(item)
    return out


def select_email_sample(
    outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic stratified email selection by stage (outcome_type)."""
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for o in outcomes:
        stage = str(o.get("stage"))
        by_stage.setdefault(stage, []).append(o)
    out: list[dict[str, Any]] = []
    for stage, size in EMAIL_STRATA_SIZES.items():
        pool = sorted(by_stage.get(stage, []), key=lambda o: str(o.get("id")))
        for o in pool[:size]:
            picked = dict(o)
            picked["_stratum"] = stage
            out.append(picked)
    return out


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
