"""Pydantic Read/Update schemas for OperatorProfile."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


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
    applicant_cap: int
    staffing_firm_blocklist: list[str]
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
    applicant_cap: int | None = None
    staffing_firm_blocklist: list[str] | None = None

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

    @field_validator("applicant_cap")
    @classmethod
    def _validate_applicant_cap(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("applicant_cap cannot be negative")
        return value
