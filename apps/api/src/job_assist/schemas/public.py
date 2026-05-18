"""Response shapes for the read-only public endpoints (PR #30a).

These schemas are *response-specific* — each ``PostingListItem`` nests a
company, a role, an optional salary, and an optional source which span
across multiple ORM models. Per-model schemas (``schemas/job_posting.py``
etc.) capture the persistence shape; this module captures the API
contract the frontend talks to.

Naming convention: ``...ListItem`` for paginated row entries,
``...Detail`` for single-resource shapes, ``...ListResponse`` for the
paginated envelope.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ── Embedded sub-shapes ───────────────────────────────────────────────────────


class CompanyEmbedded(BaseModel):
    """Lightweight company info embedded on a PostingListItem.

    The frontend builds the logo URL from ``domain`` via logo.dev (the
    token lives client-side per PR #27's design).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: str | None
    description: str | None
    tier: int | None


class RoleEmbedded(BaseModel):
    """The role attributes a list-view card renders inline."""

    title: str
    family: str | None
    department: str | None
    team: str | None
    seniority: str | None


class SalaryEmbedded(BaseModel):
    """Salary numbers. None at every field is legal — adapters often
    can't pull comp from public ATS endpoints. Caller renders the whole
    block as null when every field is None."""

    min: int | None
    max: int | None
    currency: str | None
    period: str | None


class SourceEmbedded(BaseModel):
    """Where this posting was scraped from. ``url`` may be None on the
    pathological case where a job_posting has no posting_source row."""

    ats: str
    url: str | None


class DivisionEmbedded(BaseModel):
    """Nested on PostingDetail when a matching division row exists.

    Matched via (target_company_id, department, team) with PG's
    ``IS NOT DISTINCT FROM`` semantics so NULL = NULL.
    """

    id: uuid.UUID
    department: str | None
    team: str | None
    description: str | None


# ── List items ────────────────────────────────────────────────────────────────


class PostingListItem(BaseModel):
    """Row shape for ``GET /postings``."""

    id: uuid.UUID
    company: CompanyEmbedded
    role: RoleEmbedded
    location_raw: str | None
    locations_normalized: list[str]
    remote_type: str | None
    salary: SalaryEmbedded | None
    source: SourceEmbedded
    first_seen_at: datetime
    # Placeholder for the future scoring feature. Always null today; kept
    # in the contract so the frontend can render the slot from day one.
    score: float | None = None


class PostingDetail(PostingListItem):
    """Single-resource shape for ``GET /postings/{id}``.

    Extends the list item with full description, optional division
    match, and lifecycle timestamps.
    """

    description_markdown: str | None
    division: DivisionEmbedded | None
    posted_at: datetime | None
    last_seen_at: datetime | None
    closed_at: datetime | None


class CompanyListItem(BaseModel):
    """Row shape for ``GET /companies`` — counts come from the SQL aggregation."""

    id: uuid.UUID
    name: str
    domain: str | None
    description: str | None
    tier: int | None
    ats_set: list[str]
    active_postings: int
    total_postings: int


class OutcomeListItem(BaseModel):
    """Row shape for ``GET /outcomes``. Renamed columns map to UI semantics."""

    id: uuid.UUID
    posting_id: uuid.UUID | None
    received_at: datetime
    stage: str  # = outcome_event.outcome_type
    confidence: float | None  # = outcome_event.classifier_confidence


# ── Pagination envelope ──────────────────────────────────────────────────────


class PaginatedResponse[Item: BaseModel](BaseModel):
    """Standard envelope for the list endpoints."""

    total: int
    offset: int
    limit: int
    items: list[Item]


class PostingsListResponse(PaginatedResponse[PostingListItem]):
    pass


class CompaniesListResponse(PaginatedResponse[CompanyListItem]):
    pass


class OutcomesListResponse(PaginatedResponse[OutcomeListItem]):
    pass
