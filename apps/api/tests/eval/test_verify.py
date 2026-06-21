"""Tests for the Phase 2 verify surface (build) + override scoring."""

from __future__ import annotations

from typing import Any

import pytest

from job_assist.eval.verify import (
    NA_NON_PM,
    build_workbook,
    finalize,
    read_verify_rows,
    score,
)


def _jd(
    rid: str, *, stratum: str, role_family: str, seniority: str, sha: str = "h"
) -> dict[str, Any]:
    return {
        "kind": "jd",
        "id": rid,
        "stratum": stratum,
        "input": {"title": f"T{rid}", "jd_text": f"body {rid}"},
        "input_sha256": sha,
        "model_id": "o3-xxxx",
        "openai_label": {"role_family": role_family, "seniority_level": seniority},
    }


def _email(rid: str, *, stratum: str, prod: str, outcome: str) -> dict[str, Any]:
    return {
        "kind": "email",
        "id": rid,
        "stratum": stratum,
        "production_outcome_type": prod,
        "input": {"subject": f"S{rid}", "raw_snippet": f"snip {rid}"},
        "input_sha256": "e",
        "model_id": "o3-xxxx",
        "openai_label": {"outcome_type": outcome},
    }


def _sample() -> list[dict[str, Any]]:
    return [
        _jd("j1", stratum="triage", role_family="product_management", seniority="pm"),
        _jd("j2", stratum="hard_seniority_mismatch", role_family="other", seniority="senior_pm"),
        _jd("j3", stratum="applied", role_family="product_owner", seniority="apm"),
        _email(
            "e1",
            stratum="application_confirmation",
            prod="application_confirmation",
            outcome="application_confirmation",
        ),
        _email(
            "e2",
            stratum="rejection_post_screen",
            prod="rejection_post_screen",
            outcome="rejection_pre_screen",
        ),
    ]


def test_build_prefills_and_blanks() -> None:
    wb = build_workbook(_sample())
    jd_rows, email_rows = read_verify_rows(wb)

    by_id = {r["id"]: r for r in jd_rows}
    # role_family always pre-filled = o3
    assert by_id["j1"]["verified_role_family"] == "product_management"
    # seniority pre-filled for non-mismatch...
    assert by_id["j1"]["verified_seniority"] == "pm"
    # ...blank for the hard_seniority_mismatch row (no anchor)
    assert by_id["j2"]["verified_seniority"] is None
    assert by_id["j2"]["verified_role_family"] == "other"  # rf still pre-filled

    e_by_id = {r["id"]: r for r in email_rows}
    assert e_by_id["e1"]["verified_outcome_type"] == "application_confirmation"
    # rejection-stage row is blank (no anchor)
    assert e_by_id["e2"]["verified_outcome_type"] is None


def test_build_has_lists_sheet_and_validations() -> None:
    wb = build_workbook(_sample())
    assert "_lists" in wb.sheetnames
    assert wb["_lists"].sheet_state == "hidden"
    # n/a_non_pm is an allowed seniority option in the lists sheet
    col_b = [wb["_lists"][f"B{i}"].value for i in range(2, 12)]
    assert NA_NON_PM in col_b
    assert len(wb["JDs"].data_validations.dataValidation) >= 2
    assert len(wb["Emails"].data_validations.dataValidation) >= 1


def test_score_overrides_exclusions_and_incomplete() -> None:
    prelabels = _sample()
    # Operator edits:
    jd_rows = [
        # j1: role_family changed but still PM-family (override), seniority kept
        {"id": "j1", "verified_role_family": "product_owner", "verified_seniority": "pm"},
        # j2: mismatch row — operator marks non-PM seniority N/A (excluded), rf kept other
        {"id": "j2", "verified_role_family": "other", "verified_seniority": NA_NON_PM},
        # j3: PM family, seniority changed apm->pm (override, eligible)
        {"id": "j3", "verified_role_family": "product_owner", "verified_seniority": "pm"},
    ]
    email_rows = [
        # e1: unchanged
        {"id": "e1", "verified_outcome_type": "application_confirmation"},
        # e2: rejection row, operator labeled post_screen (o3 said pre_screen) -> override
        {"id": "e2", "verified_outcome_type": "rejection_post_screen"},
    ]
    verified, summary = score(prelabels, jd_rows, email_rows)

    rf = summary["jd"]["role_family"]
    assert rf["overrides"] == 1 and rf["scored"] == 3  # j1 changed

    sen = summary["jd"]["seniority"]
    assert sen["excluded_na_non_pm"] == 1  # j2
    assert sen["n_eligible"] == 2  # j1, j3
    assert sen["overrides"] == 1  # j3 changed apm->pm

    ot = summary["email"]["outcome_type"]
    assert ot["scored"] == 2 and ot["overrides"] == 1  # e2

    # identical-input hash carried forward verbatim
    j1 = next(r for r in verified if r["id"] == "j1")
    assert j1["input_sha256"] == "h"
    assert j1["seniority_eval_eligible"] is True
    j2 = next(r for r in verified if r["id"] == "j2")
    assert j2["seniority_eval_eligible"] is False
    assert j2["verified_label"]["seniority_level"] is None  # N/A normalized to None


def test_finalize_recovers_o3_from_prefills_and_relabels_anchor_rows() -> None:
    # Build sheet (o3 prefills): a normal PM row + an anti-anchor mismatch row
    # (seniority blank) + a normal email + a rejection email (outcome blank).
    build_jd = [
        {
            "id": "j1",
            "stratum": "triage",
            "title": "PM",
            "jd_text": "x",
            "verified_role_family": "product_management",
            "verified_seniority": "pm",
        },
        {
            "id": "j2",
            "stratum": "hard_seniority_mismatch",
            "title": "Director",
            "jd_text": "y",
            "verified_role_family": "product_owner",
            "verified_seniority": None,
        },
    ]
    build_em = [
        {
            "id": "e1",
            "stratum": "application_confirmation",
            "subject": "got it",
            "raw_snippet": "s",
            "verified_outcome_type": "application_confirmation",
        },
        {
            "id": "e2",
            "stratum": "rejection_post_screen",
            "subject": "no",
            "raw_snippet": "s",
            "verified_outcome_type": None,
        },
    ]
    # Corrected sheet (operator final): j2 kept PM-family with a cold seniority;
    # e2 labeled a rejection stage cold.
    corr_jd = [
        {"id": "j1", "verified_role_family": "product_management", "verified_seniority": "pm"},
        {"id": "j2", "verified_role_family": "product_owner", "verified_seniority": "lead_pm"},
    ]
    corr_em = [
        {"id": "e1", "verified_outcome_type": "application_confirmation"},
        {"id": "e2", "verified_outcome_type": "rejection_pre_screen"},
    ]

    # Stub relabel: fresh o3 says senior_pm for the mismatch JD, pre_screen for
    # the rejection email.
    calls = {"jd": 0, "em": 0}

    def relabel_jd(title: str, jd_text: str) -> str:
        calls["jd"] += 1
        return "senior_pm"

    def relabel_em(subject: str, snippet: str) -> str:
        calls["em"] += 1
        return "rejection_pre_screen"

    prelabels, _verified, summary = finalize(
        build_jd, build_em, corr_jd, corr_em, relabel_jd=relabel_jd, relabel_em=relabel_em
    )

    # Only the 2 anti-anchor rows are relabeled (not the prefilled ones).
    assert calls == {"jd": 1, "em": 1}
    assert summary["relabeled_anchor_rows"] == 2

    # j2 seniority now scored: o3=senior_pm (fresh) vs verified=lead_pm → override.
    sen = summary["jd"]["seniority"]
    assert sen["n_eligible"] == 2  # j1 + j2 both PM-family + filled
    assert sen["overrides"] == 1  # j2 differs
    assert sen.get("unscored_o3_missing_mismatch") is None  # nothing missing now

    # e2 outcome now scored: o3=pre_screen (fresh) vs verified=pre_screen → agree.
    ot = summary["email"]["outcome_type"]
    assert ot["scored"] == 2 and ot["overrides"] == 0

    # input_sha256 present on every reconstructed prelabel; o3_source recorded.
    j2 = next(r for r in prelabels if r["id"] == "j2")
    assert j2["input_sha256"] and j2["o3_source"] == "fresh_relabel"
    j1 = next(r for r in prelabels if r["id"] == "j1")
    assert j1["o3_source"] == "build_prefill"


def test_finalize_rejects_filled_build_sheet() -> None:
    """A build sheet with no anti-anchor blanks (a filled/corrected copy) must
    fail loud, not silently score 0% override."""
    filled_jd = [
        {
            "id": "j1",
            "stratum": "hard_seniority_mismatch",
            "title": "Director",
            "jd_text": "y",
            "verified_role_family": "other",
            "verified_seniority": "senior_pm",
        },
    ]
    filled_em = [
        {
            "id": "e1",
            "stratum": "rejection_post_screen",
            "subject": "no",
            "raw_snippet": "s",
            "verified_outcome_type": "rejection_pre_screen",
        },
    ]
    corr_jd = [{"id": "j1", "verified_role_family": "other", "verified_seniority": NA_NON_PM}]
    corr_em = [{"id": "e1", "verified_outcome_type": "rejection_post_screen"}]
    with pytest.raises(ValueError, match="anti-anchor blanks"):
        finalize(
            filled_jd,
            filled_em,
            corr_jd,
            corr_em,
            relabel_jd=lambda t, j: "x",
            relabel_em=lambda s, n: "x",
        )


def test_score_blank_seniority_is_incomplete_not_agreement() -> None:
    prelabels = [
        _jd(
            "j9",
            stratum="hard_seniority_mismatch",
            role_family="product_management",
            seniority="lead_pm",
        )
    ]
    jd_rows = [
        {"id": "j9", "verified_role_family": "product_management", "verified_seniority": None}
    ]
    _, summary = score(prelabels, jd_rows, [])
    sen = summary["jd"]["seniority"]
    assert sen["incomplete_blank"] == 1
    assert sen["n_eligible"] == 0
    assert sen["overrides"] == 0
