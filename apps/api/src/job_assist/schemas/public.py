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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_assist.db.enums import ActionReason, ActionType

# ── Sort keys (PR #49) ────────────────────────────────────────────────────────

# Sort options for ``GET /postings``. Operator-facing labels live on the
# frontend (``SortDropdown.tsx``); this is the wire vocabulary. FastAPI
# rejects unknown values at the query-param boundary with a 422.
#
# Column mapping (in main.py.list_postings):
#   newest             → job_posting.first_seen_at DESC
#   oldest             → job_posting.first_seen_at ASC
#   salary_high_to_low → job_posting.salary_max DESC NULLS LAST
#   tier               → target_company.tier ASC NULLS LAST (T1 = best)
#   recently_posted    → job_posting.posted_at DESC NULLS LAST
#   best_fit (PR #57)  → job_posting.fit_score DESC NULLS LAST
#                        (index-backed by idx_job_posting_fit_score_desc_nulls_last)
#   best_fit_semantic  → (1-w)*fit_score + w*COALESCE(similarity_score, fit_score)
#     (Slice 2b)          DESC NULLS LAST, where w = operator_profile.similarity_weight
#                         (0 = off → byte-identical to best_fit). Un-embedded rows
#                         (similarity_score NULL) fall back to fit_score.
#
# Every sort gets ``job_posting.id ASC`` as a tiebreaker so pagination
# stays stable when many rows share a same-second timestamp or a NULL
# salary / tier / score.
SortKey = Literal[
    "newest",
    "oldest",
    "salary_high_to_low",
    "tier",
    "recently_posted",
    "best_fit",
    "best_fit_semantic",
]
DEFAULT_SORT: SortKey = "newest"

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


# feat/manual-application-status: the manual lifecycle stage. ``None`` ==
# the operator hasn't set a manual status (still governed by triage / Gmail
# computed state). Wire vocabulary mirrors APPLICATION_STATUS_VALUES on the
# ORM model.
ResolvedStatus = Literal["applied", "interview", "offer", "accepted", "rejected"]


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
    # feat/manual-application-status: the resolved lifecycle status that drives
    # the Applied / Rejected tabs. resolved_status =
    # COALESCE(manual application_state.status, computed company-level state).
    # ``None`` when the posting is neither manually statused nor in the
    # applied/rejected funnel. The Applied tab = resolved_status IN
    # (applied, interview, offer); Rejected tab = resolved_status == rejected.
    resolved_status: ResolvedStatus | None = None
    # feat/manual-application-status: INFORMATIONAL only — True when a
    # company-level Gmail rejection exists for this posting's company. The UI
    # shows it as a hint ("Gmail saw a rejection from {company}"); it does NOT
    # move the card (the manual status button is authoritative).
    gmail_rejection_hint: bool = False


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
    # PR #57: wired to ``job_posting.fit_score`` (PR #56's heuristic). The
    # column is INTEGER 0-100, exposed here as ``float`` because the
    # original placeholder was typed that way and changing it would be a
    # gratuitous wire-contract churn. Postings ingested before PR #56's
    # backfill landed will have NULL until the sweep visits them.
    #
    # Bestiary: when adding a new field to a response shape, grep for
    # the field name across the codebase before declaring it shipped.
    # PR #56 added the column + this schema line but left the response
    # serializer in main.py pinned at ``"score": None`` — the field
    # looked plumbed but was hidden behind a placeholder for an entire
    # release cycle. The grep-before-ship habit is the antidote.
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
    # feat/manual-source-flag: True when this company's ATS (Workday/iCIMS)
    # blocks automated ingest from the deployment's egress IP — a hand-search
    # channel. The endpoint also returns ats/ats_handle/notes/source (see
    # main.py.list_companies); this schema is documentation-only.
    manual_source: bool = False


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
    # feat/resume-version-tracking: optional tag for which tailored resume
    # variant was sent. Only valid with action_type='applied' (the service
    # + DB CHECK enforce this). NULL/omitted = untagged.
    resume_version_id: uuid.UUID | None = None


class ApplicationStatusUpdate(BaseModel):
    """Body for ``PUT /postings/{id}/status`` (feat/manual-application-status).

    The manual lifecycle stage. Pydantic rejects any value outside the five
    lifecycle stages at the boundary with a 422; the DB CHECK constraint
    (ck_application_state_status) is the backstop.
    """

    status: ResolvedStatus


class BulkPostingStateRequest(BaseModel):
    """Body for ``POST /postings/bulk-state`` (feat/bulk-triage-actions).

    Applies ONE action to many postings in a single transaction. The
    action/reason/snooze cross-field rules are identical for every id, so the
    service validates them once. ``not_interested`` REQUIRES a ``reason`` (the
    DB CHECK ck_posting_action_reason_required_for_not_interested) — a bulk
    "Pass" must supply one. ``reset`` (bulk-undo) takes no reason.
    """

    ids: list[uuid.UUID]
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
