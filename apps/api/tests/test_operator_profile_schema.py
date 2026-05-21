"""Sync Pydantic-schema tests for OperatorProfileUpdate (PR #43).

The DB-gated end-to-end tests live in ``test_operator_profile.py``; this
file covers the new field validators (salary_ceiling, seniority_levels)
without spinning up a session.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_assist.schemas.operator_profile import OperatorProfileUpdate


# ── 1 ────────────────────────────────────────────────────────────────────────
def test_salary_ceiling_accepts_valid_value() -> None:
    upd = OperatorProfileUpdate.model_validate({"salary_ceiling_usd": 200_000})
    assert upd.salary_ceiling_usd == 200_000


# ── 2 ────────────────────────────────────────────────────────────────────────
def test_salary_ceiling_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        OperatorProfileUpdate.model_validate({"salary_ceiling_usd": -1})


# ── 3 ────────────────────────────────────────────────────────────────────────
def test_salary_ceiling_rejects_below_floor() -> None:
    """When both bounds are in the same update, ceiling must be >= floor."""
    with pytest.raises(ValidationError) as exc:
        OperatorProfileUpdate.model_validate(
            {"salary_floor_usd": 130_000, "salary_ceiling_usd": 80_000}
        )
    assert "greater than or equal" in str(exc.value).lower()


def test_salary_ceiling_equal_to_floor_is_ok() -> None:
    """Edge case: ceiling exactly equal to floor is allowed (degenerate
    but valid range)."""
    upd = OperatorProfileUpdate.model_validate(
        {"salary_floor_usd": 100_000, "salary_ceiling_usd": 100_000}
    )
    assert upd.salary_ceiling_usd == 100_000


def test_salary_ceiling_alone_no_cross_field_check() -> None:
    """When only the ceiling is in the body, no floor comparison is forced
    — the floor's existing column value isn't visible here."""
    upd = OperatorProfileUpdate.model_validate({"salary_ceiling_usd": 50_000})
    assert upd.salary_ceiling_usd == 50_000


# ── 4 ────────────────────────────────────────────────────────────────────────
def test_seniority_levels_accepts_valid_values() -> None:
    upd = OperatorProfileUpdate.model_validate(
        {"seniority_levels_included": ["apm", "pm", "senior_pm"]}
    )
    assert upd.seniority_levels_included == ["apm", "pm", "senior_pm"]


# ── 5 ────────────────────────────────────────────────────────────────────────
def test_seniority_levels_rejects_invalid_level_name() -> None:
    with pytest.raises(ValidationError):
        OperatorProfileUpdate.model_validate(
            {"seniority_levels_included": ["staff"]}  # not in PM-specific enum
        )


# ── 6 ────────────────────────────────────────────────────────────────────────
def test_seniority_levels_dedupes_and_lowercases() -> None:
    upd = OperatorProfileUpdate.model_validate(
        {"seniority_levels_included": ["  PM ", "APM", "pm", "Apm"]}
    )
    assert upd.seniority_levels_included == ["pm", "apm"]


def test_seniority_levels_empty_list_passes() -> None:
    """Empty list = "clear filter, include all" — distinct from None
    ("don't touch the column")."""
    upd = OperatorProfileUpdate.model_validate({"seniority_levels_included": []})
    assert upd.seniority_levels_included == []


def test_seniority_levels_none_passes() -> None:
    upd = OperatorProfileUpdate.model_validate({"seniority_levels_included": None})
    assert upd.seniority_levels_included is None
