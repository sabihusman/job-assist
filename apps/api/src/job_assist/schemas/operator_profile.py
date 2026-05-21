"""Pydantic Read/Update schemas for OperatorProfile."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from job_assist.db.enums import SeniorityLevel

# PR #43: SeniorityLevel enum values the operator can pick from. We
# expose the enum value strings rather than the labels so the DB column
# (JSONB list[str]) stores canonical identifiers.
_VALID_SENIORITY_LEVELS = frozenset(level.value for level in SeniorityLevel)


def _clean_str_list(value: list[str] | None) -> list[str] | None:
    """Strip whitespace, reject empty strings, dedupe preserving order.

    Returns ``None`` unchanged so the ``Update`` schema's partial-update
    semantics ("field omitted from body" → "leave column alone") work
    naturally. Anything other than ``None`` must be a list of non-empty
    strings.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("must be a list of strings")
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"every item must be a string; got {type(item).__name__}")
        trimmed = item.strip()
        if not trimmed:
            raise ValueError("empty strings are not allowed in the list")
        if trimmed not in seen:
            seen.add(trimmed)
            out.append(trimmed)
    return out


class OperatorProfileRead(BaseModel):
    """Full singleton row as returned by ``GET /operator/profile``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    looking_for_text: str
    role_keywords: list[str]
    geo_whitelist: list[str]
    salary_floor_usd: int
    # PR #43: optional upper bound. NULL when the operator hasn't set one.
    salary_ceiling_usd: int | None
    applicant_cap: int
    staffing_firm_blocklist: list[str]
    # PR #43: list of SeniorityLevel enum values to include. NULL or empty
    # means "include all levels".
    seniority_levels_included: list[str] | None
    created_at: datetime
    updated_at: datetime


class OperatorProfileUpdate(BaseModel):
    """Partial update body for ``PUT /operator/profile``.

    Every field is optional. Fields omitted from the body leave the
    corresponding column untouched (``exclude_unset=True`` on the way
    into SQLAlchemy). Fields that ARE present run the validators below
    before they reach the DB.
    """

    model_config = ConfigDict(extra="forbid")

    looking_for_text: str | None = None
    role_keywords: list[str] | None = None
    geo_whitelist: list[str] | None = None
    salary_floor_usd: int | None = None
    # PR #43: optional ceiling (nullable in DB; this field accepts None to
    # explicitly clear it via PUT). The cross-field validator below rejects
    # ceiling < floor when both are present in the same update.
    salary_ceiling_usd: int | None = None
    applicant_cap: int | None = None
    staffing_firm_blocklist: list[str] | None = None
    # PR #43: list of SeniorityLevel enum values. None = "leave column
    # unchanged"; empty list = "clear filter" (include all levels).
    seniority_levels_included: list[str] | None = None

    @field_validator("role_keywords", "geo_whitelist", "staffing_firm_blocklist")
    @classmethod
    def _validate_list(cls, value: list[str] | None) -> list[str] | None:
        return _clean_str_list(value)

    @field_validator("salary_floor_usd")
    @classmethod
    def _validate_salary_floor(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("salary_floor_usd cannot be negative")
        return value

    @field_validator("salary_ceiling_usd")
    @classmethod
    def _validate_salary_ceiling(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("salary_ceiling_usd cannot be negative")
        return value

    @field_validator("applicant_cap")
    @classmethod
    def _validate_applicant_cap(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("applicant_cap cannot be negative")
        return value

    @field_validator("seniority_levels_included")
    @classmethod
    def _validate_seniority_levels(cls, value: list[str] | None) -> list[str] | None:
        """Lowercase + strip + dedupe; reject unknown enum values."""
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("must be a list of strings")
        seen: set[str] = set()
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"every item must be a string; got {type(item).__name__}")
            normalised = item.strip().lower()
            if not normalised:
                continue
            if normalised not in _VALID_SENIORITY_LEVELS:
                raise ValueError(
                    f"unknown seniority level {item!r}; "
                    f"must be one of {sorted(_VALID_SENIORITY_LEVELS)}"
                )
            if normalised not in seen:
                seen.add(normalised)
                out.append(normalised)
        return out

    @model_validator(mode="after")
    def _validate_ceiling_above_floor(self) -> OperatorProfileUpdate:
        """If both floor and ceiling land in the same update, ceiling must be ≥ floor.

        When only one is in the body, we can't enforce the cross-field rule
        from this payload alone (the other column's current value lives in
        the DB, not on this object). The endpoint could re-check after
        merge, but pragmatically the UI submits both together when either
        changes, so this catches the common case.
        """
        if (
            self.salary_floor_usd is not None
            and self.salary_ceiling_usd is not None
            and self.salary_ceiling_usd < self.salary_floor_usd
        ):
            raise ValueError(
                "salary_ceiling_usd must be greater than or equal to salary_floor_usd"
            )
        return self
