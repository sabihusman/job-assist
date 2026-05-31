"""Adapter Protocol, shared data models, and shared exceptions for all ATS adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel


class HandleNotFoundError(Exception):
    """Raised by an adapter when the upstream ATS returns 404 for the
    configured handle's *listing* endpoint.

    Bestiary 5.9: silent 404 swallow conflates "tenant has no postings
    right now" with "tenant migrated off this ATS / handle is wrong."
    The operator-facing impact is identical (``postings_fetched=0,
    status="success"``) and the bug class can mask stale ATS configs
    for months.

    Adapters raise this only on the FIRST (listing-level) call's 404.
    Per-job 404s during enumeration (a posting deleted between listing
    and detail fetch) continue to use the silent-return pattern —
    those are not handle-level failures.

    The orchestrator (``services/ingestion.py``) catches this and sets
    ``IngestRun.status = handle_not_found``, distinct from generic
    ``failed`` (which still covers network errors, parsing failures,
    etc.).
    """

    def __init__(self, *, ats: str, handle: str, url: str) -> None:
        self.ats = ats
        self.handle = handle
        self.url = url
        super().__init__(f"handle not found: ats={ats!r} handle={handle!r} url={url!r}")


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
    # Org-chart strings extracted from the ATS payload. Both nullable —
    # adapters set None when the source doesn't surface them. PR #28a.
    department: str | None = None
    team: str | None = None
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

    def peek_title(self, raw: RawPosting) -> str:
        """Cheap title extract for the pre-filter (Slice 1 broad-ingest).

        Returns the raw posting title without running the full
        ``normalize()`` pipeline, so ``IngestionService.ingest_source``
        can skip non-PM titles before paying normalize/upsert cost.
        Per-adapter overrides know which key in ``raw.raw_payload`` to
        read (greenhouse: ``title``, lever: ``text``, etc.); the
        default implementation falls back to ``title`` since every ATS
        we currently support uses that key in some shape.
        """
        ...
