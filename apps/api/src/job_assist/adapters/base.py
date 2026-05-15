"""Adapter Protocol and shared data models for all ATS adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel


class RawPosting(BaseModel):
    """Raw posting data from an ATS API, before normalization."""

    source_job_id: str
    raw_payload: dict[str, Any]


class NormalizedPosting(BaseModel):
    """Adapter-populated fields for JobPosting + PostingSource creation.

    Adapters populate only the fields they can derive from the ATS API.
    Fields the adapter cannot populate (e.g. salary from Greenhouse public API)
    default to None / sentinel enum values.
    """

    # ── JobPosting fields ────────────────────────────────────────────────────
    canonical_company_name: str
    normalized_title: str
    raw_title: str
    location_raw: str | None = None
    locations_normalized: list[dict[str, Any]] = []
    remote_type: str = "unknown"  # RemoteType enum value
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str = "unknown"  # SalaryPeriod enum value
    seniority_level: str = "unknown"  # SeniorityLevel enum value
    role_family: str = "other"  # RoleFamily enum value
    jd_text: str = ""
    jd_text_hash: str = ""  # sha256(jd_text)
    content_hash: str = ""  # sha256(company+title+locations)
    posted_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    should_embed: bool = False

    # ── PostingSource fields ──────────────────────────────────────────────────
    ats: str  # ATS enum value
    source_job_id: str
    source_url: str
    apply_url: str | None = None
    raw_payload: dict[str, Any]
    parser_version: str
    fetch_status: str = "ok"  # FetchStatus enum value


class Adapter(Protocol):
    """Protocol every ATS adapter must satisfy."""

    ats: ClassVar[str]
    parser_version: ClassVar[str]

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        """Fetch all active postings for one company handle."""
        ...

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Convert a raw ATS payload to a NormalizedPosting."""
        ...
