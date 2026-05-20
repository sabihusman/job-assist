"""Pydantic schemas for the contact seed endpoint (PR #39).

The validators here are stricter than the DB-level CHECK constraints so
mistakes are caught before they ever reach the DB:

* LinkedIn URLs are normalized to a canonical ``https://linkedin.com/in/<slug>``
  shape so the LOWER() unique index can actually catch dupes. The Tippie
  directory contains a mix of ``www.``-prefixed, trailing-slash, and
  scheme-less rows.
* Email fields are lowercased + stripped so case-only variants dedupe.
* List fields are stripped, empty entries removed, dupes folded out.

Privacy: the response shape (``ContactSeedResponse``) is intentionally
count-only. No names, emails, or LinkedIn URLs appear in it; the seed
endpoint and CLI log success/skip messages from these counts too.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_VALID_SOURCE_TYPES = frozenset(
    {"tippie_alumni", "linkedin_outreach", "recruiter_inbound", "warm_intro"}
)

# Recognises the canonical and the most common variants. The regex is
# anchored on the host so a trailing path (``/in/foo``, ``/in/foo/``,
# ``/in/foo/?utm=...``) can be normalised down to just the slug.
_LINKEDIN_HOST_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?linkedin\.com/in/(?P<slug>[^/?#]+)",
    re.IGNORECASE,
)


def _normalize_linkedin_url(raw: str) -> str:
    """Return ``https://linkedin.com/in/<slug>`` for any recognised input.

    Raises ``ValueError`` for anything that doesn't match the LinkedIn
    ``/in/`` pattern. Query strings and trailing slashes are dropped so
    two operator-entered variants of the same profile dedupe cleanly.
    """
    trimmed = raw.strip()
    m = _LINKEDIN_HOST_RE.match(trimmed)
    if not m:
        raise ValueError(
            "linkedin_url must be a LinkedIn profile URL (e.g. https://linkedin.com/in/<slug>)"
        )
    slug = m.group("slug").strip("/")
    if not slug:
        raise ValueError("linkedin_url is missing the profile slug")
    return f"https://linkedin.com/in/{slug}"


def _clean_str_list(value: list[str] | None) -> list[str] | None:
    """Strip + dedup + drop empty entries. ``None`` passes through."""
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
            continue
        if trimmed not in seen:
            seen.add(trimmed)
            out.append(trimmed)
    return out


def _clean_optional_str(value: str | None) -> str | None:
    """Strip; treat empty string as ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _clean_email(value: str | None) -> str | None:
    """Lowercase + strip; empty → ``None``. We do NOT run RFC-5321
    validation — the source xlsx has free-form addresses and we'd rather
    accept a slightly-off row than drop the whole batch."""
    if value is None:
        return None
    trimmed = value.strip().lower()
    return trimmed or None


class ContactSeedRow(BaseModel):
    """One row of the JSON body posted to ``/admin/seed/contacts``.

    Required: ``first_name``, ``last_name``, ``source_type``, plus at
    least one of ``email_primary`` or ``linkedin_url`` (DB CHECK
    constraint, mirrored at this validation layer for a clean 4xx).
    """

    model_config = ConfigDict(extra="ignore")

    first_name: str
    last_name: str
    preferred_first_name: str | None = None
    email_primary: str | None = None
    email_secondary: str | None = None
    linkedin_url: str | None = None
    current_employer: str | None = None
    current_position: str | None = None
    location_city: str | None = None
    location_state: str | None = None
    location_country: str | None = None
    location_metro: str | None = None
    source_type: str
    source_metadata: dict[str, object] | None = None
    job_functions_of_interest: list[str] | None = None
    industries_of_interest: list[str] | None = None
    contact_opt_in: bool = False
    contact_opt_in_topics: list[str] | None = None
    notes: str | None = None

    # ── field validators ────────────────────────────────────────────────

    @field_validator("first_name", "last_name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        trimmed = value.strip() if isinstance(value, str) else ""
        if not trimmed:
            raise ValueError("must be a non-empty string")
        return trimmed

    @field_validator(
        "preferred_first_name",
        "current_employer",
        "current_position",
        "location_city",
        "location_state",
        "location_country",
        "location_metro",
        "notes",
    )
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        return _clean_optional_str(value)

    @field_validator("email_primary", "email_secondary")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        return _clean_email(value)

    @field_validator("linkedin_url")
    @classmethod
    def _normalize_linkedin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        return _normalize_linkedin_url(trimmed)

    @field_validator("source_type")
    @classmethod
    def _validate_source_type(cls, value: str) -> str:
        trimmed = value.strip() if isinstance(value, str) else ""
        if trimmed not in _VALID_SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of {sorted(_VALID_SOURCE_TYPES)}; got {trimmed!r}"
            )
        return trimmed

    @field_validator(
        "job_functions_of_interest",
        "industries_of_interest",
        "contact_opt_in_topics",
    )
    @classmethod
    def _validate_list(cls, value: list[str] | None) -> list[str] | None:
        return _clean_str_list(value)

    # ── cross-field ─────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _require_channel(self) -> ContactSeedRow:
        if self.email_primary is None and self.linkedin_url is None:
            raise ValueError("at least one of email_primary or linkedin_url must be provided")
        return self


class ContactSeedResponse(BaseModel):
    """Count-only response — no PII leaks out of the seed endpoint."""

    inserted: int
    skipped_duplicate_email: int
    skipped_duplicate_linkedin: int
    skipped_invalid: int
    total: int
