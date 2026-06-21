"""Tests for the Phase 3 Gemini scorer — pure aggregation + the input lock.
Stubbed classifiers; no Gemini calls."""

from __future__ import annotations

from typing import Any

from job_assist.eval.gemini_score import _input_sha256, aggregate, collect


def _jd_row(
    rid: str,
    *,
    title: str,
    jd: str,
    v_rf: str,
    v_sen: str | None,
    o3_rf: str,
    o3_sen: str,
    eligible: bool,
) -> dict[str, Any]:
    inp = {"title": title, "jd_text": jd}
    return {
        "kind": "jd",
        "id": rid,
        "input": inp,
        "input_sha256": _input_sha256(inp),
        "seniority_eval_eligible": eligible,
        "o3_label": {"role_family": o3_rf, "seniority_level": o3_sen},
        "verified_label": {"role_family": v_rf, "seniority_level": v_sen},
    }


def _em_row(rid: str, *, subject: str, snip: str, v_ot: str, o3_ot: str) -> dict[str, Any]:
    inp = {"subject": subject, "raw_snippet": snip}
    return {
        "kind": "email",
        "id": rid,
        "input": inp,
        "input_sha256": _input_sha256(inp),
        "o3_label": {"outcome_type": o3_ot},
        "verified_label": {"outcome_type": v_ot},
    }


async def test_collect_enforces_input_sha256_lock() -> None:
    good = _jd_row(
        "j1",
        title="PM",
        jd="body",
        v_rf="product_management",
        v_sen="pm",
        o3_rf="product_management",
        o3_sen="pm",
        eligible=True,
    )
    bad = _jd_row(
        "j2",
        title="PM",
        jd="body",
        v_rf="product_management",
        v_sen="pm",
        o3_rf="product_management",
        o3_sen="pm",
        eligible=True,
    )
    bad["input_sha256"] = "tampered"

    async def cj(title: str, jd: str) -> tuple[str, str]:
        return ("product_management", "senior_pm")

    async def ce(subject: str, snip: str) -> str:
        return "offer"

    scored, skipped = await collect([good, bad], classify_jd=cj, classify_email=ce)
    assert [s["id"] for s in scored] == ["j1"]
    assert skipped == [{"id": "j2", "kind": "jd", "reason": "input_sha256_mismatch"}]
    # Gemini label captured from the stub.
    assert scored[0]["gemini"] == {
        "role_family": "product_management",
        "seniority_level": "senior_pm",
    }


def test_aggregate_three_way_accuracy_and_under_leveling() -> None:
    scored = [
        # role_family: gemini right, o3 right
        {
            "kind": "jd",
            "id": "j1",
            "eligible": True,
            "verified": {"role_family": "product_management", "seniority_level": "senior_pm"},
            "o3": {"role_family": "product_management", "seniority_level": "senior_pm"},
            "gemini": {"role_family": "product_management", "seniority_level": "unknown"},
        },
        # role_family: gemini wrong, o3 right
        {
            "kind": "jd",
            "id": "j2",
            "eligible": True,
            "verified": {"role_family": "product_owner", "seniority_level": "pm"},
            "o3": {"role_family": "product_owner", "seniority_level": "pm"},
            "gemini": {"role_family": "other", "seniority_level": "pm"},
        },
        # not eligible for seniority (excluded), role_family still counts
        {
            "kind": "jd",
            "id": "j3",
            "eligible": False,
            "verified": {"role_family": "other", "seniority_level": None},
            "o3": {"role_family": "other", "seniority_level": None},
            "gemini": {"role_family": "other", "seniority_level": "unknown"},
        },
        {
            "kind": "email",
            "id": "e1",
            "verified": {"outcome_type": "rejection_post_screen"},
            "o3": {"outcome_type": "rejection_pre_screen"},
            "gemini": {"outcome_type": "rejection_post_screen"},
        },
    ]
    summary = aggregate(scored, [], profile_context_used=False)

    rf = summary["role_family"]
    assert rf["n"] == 3
    assert rf["gemini"]["correct"] == 2  # j1, j3 (j2 wrong)
    assert rf["o3"]["correct"] == 3

    sen = summary["seniority"]
    assert sen["n_eligible"] == 2  # j1, j2 (j3 not eligible)
    assert sen["gemini"]["correct"] == 1  # j1 gemini=unknown wrong, j2 gemini=pm right
    assert sen["o3"]["correct"] == 2
    # Gemini under-levels j1 (unknown < senior_pm); j2 pm==pm not under. o3 none.
    assert sen["gemini_under_leveled"] == 1
    assert sen["o3_under_leveled"] == 0

    ot = summary["outcome_type"]
    assert ot["n"] == 1
    assert ot["gemini"]["correct"] == 1
    assert ot["o3"]["correct"] == 0  # o3 said pre_screen, verified post_screen
    assert summary["profile_context_used"] is False


def test_aggregate_skips_recorded() -> None:
    summary = aggregate(
        [],
        [{"id": "x", "kind": "jd", "reason": "input_sha256_mismatch"}],
        profile_context_used=True,
    )
    assert summary["n_skipped"] == 1
    assert summary["profile_context_used"] is True
