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

from pydantic import BaseModel, ConfigDict, Field

from job_assist.db.enums import ActionReason, ActionType

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


class StateEmbedded(BaseModel):
    """Operator's current decision state on a posting (PR #31).

    ``current is None`` means the posting has no action rows yet — it's
    still in triage. A ``current == ActionType.reset`` means the operator
    explicitly returned it to triage; the frontend treats it the same as
    ``None`` for filtering but the audit trail in ``state_history``
    preserves the distinction.
    """

    current: ActionType | None
    reason: ActionReason | None
    snooze_until: datetime | None
    current_at: datetime | None


class PostingActionItem(BaseModel):
    """One row in PostingDetail.state_history (PR #31).

    Chronological ASC ordering is the contract; the UI doesn't have to
    sort. Includes notes even though no current endpoint sets them —
    keeps the contract stable when the notes endpoint lands.
    """

    id: uuid.UUID
    action_type: ActionType
    reason: ActionReason | None
    snooze_until: datetime | None
    notes: str | None
    created_at: datetime


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
    # Operator's current triage state. Always present; the nested fields
    # are null for postings the operator hasn't touched yet (PR #31).
    state: StateEmbedded


class PostingDetail(PostingListItem):
    """Single-resource shape for ``GET /postings/{id}``.

    Extends the list item with full description, optional division
    match, and lifecycle timestamps.
    """

    description_markdown: str | None
    # Gemini-generated operator-focused summary (PR #41/#42). NULL until
    # the enrichment sweep has visited the row — the frontend shows the
    # raw description in that case + a "pending" footnote.
    jd_summary_markdown: str | None
    division: DivisionEmbedded | None
    posted_at: datetime | None
    last_seen_at: datetime | None
    closed_at: datetime | None
    # Full append-only action log, oldest → newest (PR #31).
    state_history: list[PostingActionItem]


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


# ── PR #31 — request bodies ──────────────────────────────────────────────────


class PostingStateRequest(BaseModel):
    """Body for ``POST /postings/{id}/state``.

    The full cross-field rule set (reason required iff not_interested,
    snooze_until only with snoozed, no past timestamps) lives in the
    service layer so the validation errors include posting context and
    so the DB CHECK constraints catch any drift.
    """

    action_type: ActionType
    reason: ActionReason | None = None
    snooze_until: datetime | None = None
    notes: str | None = None


# ── PR #30b — stats response shapes ──────────────────────────────────────────


class StatsWindow(BaseModel):
    """The resolved ``[since, until]`` window echoed back to the client.

    Serialised to ISO-8601 so the frontend can render "Showing X for
    {window_label}" without round-tripping its own date parser.
    """

    since: datetime
    until: datetime


class TopRejectedRoleFamily(BaseModel):
    """One row in ``calibration.top_rejected_role_families``."""

    role_family: str
    count: int


class CalibrationResponse(BaseModel):
    """Body of ``GET /stats/calibration``.

    ``interested_rate`` is ``interested / surfaced`` rounded to 2dp, or
    ``None`` when ``surfaced == 0`` (the frontend renders "—" rather
    than 0%).
    """

    window: StatsWindow
    surfaced: int
    interested: int
    interested_rate: float | None
    applied: int
    rejected_by_you: int
    top_rejected_role_families: list[TopRejectedRoleFamily]


class FunnelStage(BaseModel):
    name: str
    count: int


class FunnelConversionRate(BaseModel):
    """One row in ``funnel.conversion_rates``.

    ``from`` collides with the Python keyword, so we alias the wire
    field to ``from_stage`` internally and let Pydantic populate it
    from either name on parse / serialise.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_stage: str = Field(alias="from")
    to: str
    rate: float | None


class FunnelResponse(BaseModel):
    """Body of ``GET /stats/funnel``. Stages always returned in the
    fixed order ``[surfaced, interested, applied]``."""

    window: StatsWindow
    stages: list[FunnelStage]
    conversion_rates: list[FunnelConversionRate]
