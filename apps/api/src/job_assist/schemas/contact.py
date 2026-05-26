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
import uuid
from datetime import datetime

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


# ── PR #51 — list endpoint shapes ─────────────────────────────────────────────


class ContactListItem(BaseModel):
    """Row shape for ``GET /contacts``.

    Surfaces only the fields the read-only Contacts list page needs. The
    seed endpoint's richer write-side schema (``ContactSeedRow``) stays
    separate — different concerns, different validators.

    PII note: this DOES carry names + email + LinkedIn URL by design —
    those are exactly what the operator needs to act. The endpoint
    paginates (caller-supplied ``limit`` capped at 100) so a single
    response can't dump the full directory.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    preferred_first_name: str | None
    email_primary: str | None
    email_secondary: str | None
    linkedin_url: str | None
    current_employer: str | None
    current_position: str | None
    location_city: str | None
    location_state: str | None
    location_country: str | None
    location_metro: str | None
    source_type: str
    target_company_id: uuid.UUID | None
    archived_at: datetime | None
    created_at: datetime


class ContactsListResponse(BaseModel):
    """Paginated envelope for ``GET /contacts``."""

    total: int
    offset: int
    limit: int
    items: list[ContactListItem]


# ── PR #52 — detail + CRUD shapes ────────────────────────────────────────────


class ContactDetail(BaseModel):
    """Full row shape for ``GET /contacts/{id}``.

    Strict superset of :class:`ContactListItem` — adds the heavy /
    operator-only fields the detail panel needs: ``notes``,
    ``contact_opt_in`` + ``contact_opt_in_topics``,
    ``source_metadata``, ``job_functions_of_interest``,
    ``industries_of_interest``, ``phone``, ``updated_at``.

    Split from the list endpoint deliberately — the Contacts page
    list query paginates and stays lean; the detail panel pulls
    these heavier fields only when the operator opens a row. Same
    pattern as ``GET /postings`` vs ``GET /postings/{id}``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    preferred_first_name: str | None
    email_primary: str | None
    email_secondary: str | None
    linkedin_url: str | None
    phone: str | None
    current_employer: str | None
    current_position: str | None
    location_city: str | None
    location_state: str | None
    location_country: str | None
    location_metro: str | None
    source_type: str
    source_metadata: dict[str, object] | None
    job_functions_of_interest: list[str] | None
    industries_of_interest: list[str] | None
    contact_opt_in: bool
    contact_opt_in_topics: list[str] | None
    notes: str | None
    target_company_id: uuid.UUID | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContactCreate(BaseModel):
    """Body for ``POST /contacts`` — operator-driven create.

    Distinct from :class:`ContactSeedRow` (which is the xlsx-import
    shape with stricter normalisation and a count-only response).
    This one returns a full :class:`ContactDetail` so the frontend
    can immediately show the created row in the detail panel.

    Reachability + source_type rules mirror the DB CHECK constraints
    so the operator gets a clean 422 instead of an opaque PG error.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str
    last_name: str
    preferred_first_name: str | None = None
    email_primary: str | None = None
    email_secondary: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
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
    target_company_id: uuid.UUID | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        trimmed = value.strip() if isinstance(value, str) else ""
        if not trimmed:
            raise ValueError("must be a non-empty string")
        return trimmed

    @field_validator(
        "preferred_first_name",
        "phone",
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

    @model_validator(mode="after")
    def _require_channel(self) -> ContactCreate:
        if self.email_primary is None and self.linkedin_url is None:
            raise ValueError("at least one of email_primary or linkedin_url must be provided")
        return self


class ContactUpdate(BaseModel):
    """Body for ``PATCH /contacts/{id}`` — partial update.

    Only mutable fields are accepted. Immutable fields (``id``,
    ``created_at``, ``source_type``, ``first_name``, ``last_name``)
    are intentionally absent from this schema; passing them via
    ``extra='forbid'`` returns 422 with a clean message rather than
    silently dropping them. Operators who think a name is wrong
    should archive + re-create rather than rename.

    Every field is ``Optional`` with a sentinel default — only fields
    present in the request body (``exclude_unset=True`` at apply time)
    are touched. Sending ``"notes": null`` explicitly clears notes;
    omitting the key leaves the existing value alone.

    Reachability after update is re-checked in the endpoint, not here,
    because the validator only sees the partial diff — it can't know
    what's already in the row.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_first_name: str | None = None
    email_primary: str | None = None
    email_secondary: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
    current_employer: str | None = None
    current_position: str | None = None
    location_city: str | None = None
    location_state: str | None = None
    location_country: str | None = None
    location_metro: str | None = None
    source_metadata: dict[str, object] | None = None
    job_functions_of_interest: list[str] | None = None
    industries_of_interest: list[str] | None = None
    contact_opt_in: bool | None = None
    contact_opt_in_topics: list[str] | None = None
    notes: str | None = None
    target_company_id: uuid.UUID | None = None

    @field_validator(
        "preferred_first_name",
        "phone",
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

    @field_validator(
        "job_functions_of_interest",
        "industries_of_interest",
        "contact_opt_in_topics",
    )
    @classmethod
    def _validate_list(cls, value: list[str] | None) -> list[str] | None:
        return _clean_str_list(value)
