"""Unit tests for the stratified sample selection (pure, no network)."""

from __future__ import annotations

from typing import Any

from job_assist.eval.sample import (
    EMAIL_STRATA_SIZES,
    is_disguised_comp,
    is_hard_seniority_mismatch,
    resolved_bucket,
    select_email_sample,
    select_jd_sample,
)


def _jd(
    pid: str,
    *,
    title: str = "Product Manager",
    seniority: str = "pm",
    salary_min: int | None = None,
    resolved: str | None = None,
    current: str | None = None,
) -> dict[str, Any]:
    return {
        "id": pid,
        "role": {"title": title, "seniority": seniority, "family": "product_management"},
        "salary": {"min": salary_min, "currency": "USD"} if salary_min else None,
        "state": {"resolved_status": resolved, "current": current},
    }


def test_resolved_bucket_maps_states() -> None:
    assert resolved_bucket(_jd("1", resolved="applied")) == "applied"
    assert resolved_bucket(_jd("2", current="not_interested")) == "passed"
    assert resolved_bucket(_jd("3")) == "triage"


def test_hard_mismatch_and_disguised_predicates() -> None:
    # Director title but parsed pm → mismatch (under-leveled).
    assert is_hard_seniority_mismatch(_jd("1", title="Director, Product", seniority="pm"))
    # Senior-correct title at senior_pm is NOT a mismatch.
    assert not is_hard_seniority_mismatch(
        _jd("2", title="Director, Product", seniority="senior_pm")
    )
    # High comp at unknown seniority → disguised.
    assert is_disguised_comp(_jd("3", seniority="unknown", salary_min=200_000))
    assert not is_disguised_comp(_jd("4", seniority="senior_pm", salary_min=200_000))


def test_select_jd_sample_is_deterministic_and_deduped() -> None:
    postings = [_jd(f"{i:03d}", resolved="applied") for i in range(30)]
    a = [r["id"] for r in select_jd_sample(postings)]
    b = [r["id"] for r in select_jd_sample(postings)]
    assert a == b  # deterministic
    assert len(a) == len(set(a))  # no dupes
    # Only 'applied' rows exist → capped at the applied stratum size (17).
    assert len(a) == 17


def test_select_email_sample_caps_per_stage() -> None:
    rows: list[dict[str, Any]] = []
    for stage in ("unrelated", "application_confirmation"):
        rows += [{"id": f"{stage}-{i}", "stage": stage} for i in range(50)]
    picked = select_email_sample(rows)
    by_stage: dict[str, int] = {}
    for r in picked:
        by_stage[r["_stratum"]] = by_stage.get(r["_stratum"], 0) + 1
    assert by_stage["unrelated"] == EMAIL_STRATA_SIZES["unrelated"]  # 9
    assert by_stage["application_confirmation"] == EMAIL_STRATA_SIZES["application_confirmation"]
