"""Shared query-building for the postings view (PR feat/triage-export-xlsx).

Both ``GET /postings`` and the xlsx export need to produce the SAME slice
of rows for a given URL — same filters, same sort, same per-company cap.
Duplicating that logic across endpoints invites drift; this module is the
single source of truth.

Responsibilities (pure, no DB execution):
  * Hold the validated view-state in :class:`PostingsViewSpec`.
  * Build the shared pieces every caller needs in :func:`build_view_parts`:
      - ``where_clauses`` — the filter list (closed/filtered/tier/remote/
        role_family/target_company/ats EXISTS, plus the state predicate when
        a state filter is active).
      - ``recent_pa`` lateral + ``needs_state_lateral`` flag — the most-
        recent posting_action subquery used by the state filter; the caller
        joins it onto its SELECT only when needed.
      - ``capped_ids`` — a SELECT statement enumerating the per-company-cap
        survivors (or ``None`` when the cap is disabled). Both COUNT and
        SELECT add ``WHERE id IN (capped_ids)`` so the visible row count
        matches the visible rows.
      - ``order_clauses`` — the ORDER BY columns derived from ``sort``.

The validators (``_validate_ats_filter`` etc.) stay in ``main.py`` for now —
they raise ``HTTPException`` which is endpoint-shape, not service-shape.
Each endpoint validates its query params then constructs a spec.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import Text, and_, case, cast, func, or_, select, true
from sqlalchemy import false as sa_false
from sqlalchemy.sql import Select

from job_assist.db.models import (
    ApplicationState,
    JobPosting,
    OperatorProfile,
    OutcomeEvent,
    PostingSource,
    TargetCompany,
)
from job_assist.schemas.public import DEFAULT_SORT, SortKey
from job_assist.services.posting_actions import latest_action_lateral

# Cross-table state ``state=rejected`` matches an EXISTS row on
# ``outcome_event``. Kept in lockstep with the inline list referenced from
# ``main.py``'s comment near the state-filter code.
_REJECTION_OUTCOME_TYPES = (
    "rejection_pre_screen",
    "rejection_post_screen",
    "rejection_post_interview",
)

# feat/manual-application-status: the Applied tab keeps a card while its
# resolved status is one of these (applied AND not terminal). ``accepted`` /
# ``rejected`` fall outside → the card drops out of Applied.
_APPLIED_TAB_STATUSES = ("applied", "interview", "offer")


def gmail_rejection_exists() -> Any:
    """Correlated EXISTS: a company-level Gmail rejection for this posting.

    Company-level because ``outcome_event.job_posting_id`` is deferred-by-
    design and uniformly NULL in production (gmail/backfill.py). Defensive
    against NULL ``target_company_id`` via the explicit IS NOT NULL guard.
    """
    return (
        select(OutcomeEvent.id)
        .where(JobPosting.target_company_id.is_not(None))
        .where(OutcomeEvent.target_company_id == JobPosting.target_company_id)
        .where(OutcomeEvent.outcome_type.in_(_REJECTION_OUTCOME_TYPES))
        .exists()
    )


def _entered_applied_exists() -> Any:
    """Correlated EXISTS: a company-level Gmail application_confirmation."""
    return (
        select(OutcomeEvent.id)
        .where(JobPosting.target_company_id.is_not(None))
        .where(OutcomeEvent.target_company_id == JobPosting.target_company_id)
        .where(OutcomeEvent.outcome_type == "application_confirmation")
        .exists()
    )


def manual_status_scalar() -> Any:
    """Correlated scalar: the operator's manual ``application_state.status``
    for this posting, or NULL when no row exists."""
    return (
        select(ApplicationState.status)
        .where(ApplicationState.job_posting_id == JobPosting.id)
        .scalar_subquery()
    )


def resolved_status_expr(recent_pa: Any) -> Any:
    """resolved_status driving the Applied / Rejected tabs.

    ``COALESCE(manual application_state.status, computed company-level state)``
    where the computed fallback is::

        CASE WHEN entered_applied THEN 'applied'      -- posting_action=applied OR Gmail confirmation
             WHEN gmail_rejected  THEN 'rejected'     -- company-level Gmail rejection
             ELSE NULL END

    Two deliberate ordering choices:

      * Manual status is authoritative (COALESCE puts it first) — once the
        operator presses a status button, Gmail signal is ignored.
      * In the computed fallback, ``entered_applied`` is checked BEFORE
        ``gmail_rejected`` so a company-level Gmail rejection never pulls an
        applied-but-unresolved card out of Applied. There, the rejection is an
        informational hint only (the manual button is the sole authoritative
        mover). Gmail rejection surfaces a card as 'rejected' ONLY for roles
        the operator never entered into Applied — the untouched-role fallback.

    Needs ``recent_pa`` (the latest_action_lateral) joined onto the statement.
    """
    computed = case(
        (or_(recent_pa.c.pa_action_type == "applied", _entered_applied_exists()), "applied"),
        (gmail_rejection_exists(), "rejected"),
        else_=None,
    )
    return func.coalesce(cast(manual_status_scalar(), Text), computed)


@dataclass(frozen=True)
class PostingsViewSpec:
    """The validated filter / sort / cap state of a postings view.

    All list fields are tuples so the dataclass stays hashable and
    immutable. Construct via :meth:`from_validated` — see :func:`main.py`
    for the validation layer (which raises 422 on bad input).
    """

    tier: tuple[int, ...] = ()
    ats: tuple[str, ...] = ()
    remote_type: tuple[str, ...] = ()
    role_family: tuple[str, ...] = ()
    state: tuple[str, ...] = ()
    include_snoozed_past_only: bool = False
    target_company_id: uuid.UUID | None = None
    sort: SortKey = DEFAULT_SORT
    # feat/tunable-per-company-cap: an explicit int is an override (0 =
    # disabled). ``None`` means "use the operator's persisted
    # operator_profile.per_company_cap", resolved as an inlined SQL scalar
    # subquery in build_view_parts — so the default path adds NO extra DB
    # round-trip (the 2-query COUNT+SELECT budget is preserved).
    per_company_cap: int | None = 3
    include_closed: bool = False
    include_filtered: bool = False

    @classmethod
    def from_validated(
        cls,
        *,
        tier: list[int] | None,
        ats: list[str] | None,
        remote_type: list[str] | None,
        role_family: list[str] | None,
        state: list[str] | None,
        include_snoozed_past_only: bool,
        target_company_id: uuid.UUID | None,
        sort: SortKey,
        per_company_cap: int | None,
        include_closed: bool,
        include_filtered: bool,
    ) -> PostingsViewSpec:
        """Construct from the raw post-validation params an endpoint already
        produced. Lists become tuples (so the spec is hashable)."""
        return cls(
            tier=tuple(tier or ()),
            ats=tuple(ats or ()),
            remote_type=tuple(remote_type or ()),
            role_family=tuple(role_family or ()),
            state=tuple(state or ()),
            include_snoozed_past_only=include_snoozed_past_only,
            target_company_id=target_company_id,
            sort=sort,
            per_company_cap=per_company_cap,
            include_closed=include_closed,
            include_filtered=include_filtered,
        )


@dataclass
class PostingsQueryParts:
    """The shared building blocks both endpoints assemble into a SELECT.

    The ``base_join`` is the ``JobPosting OUTER JOIN target_company`` used
    by both the COUNT and the main SELECT. The caller adds its own SELECT
    columns + endpoint-specific laterals (the list endpoint also folds in a
    most-recent posting_source lateral and the posting_action lateral
    columns; the export endpoint adds only the posting_source lateral).
    """

    spec: PostingsViewSpec
    base_join: Any  # JobPosting.__table__.outerjoin(TargetCompany.__table__, ...)
    where_clauses: list[Any]
    recent_pa: Any  # latest_action_lateral() — used by state filter + list rows
    needs_state_lateral: bool  # True iff the state filter requires the lateral
    capped_ids: Select[Any] | None  # SELECT posting_id of per-company-cap survivors
    order_clauses: list[Any]  # ORDER BY mapped from ``spec.sort``


def build_view_parts(spec: PostingsViewSpec) -> PostingsQueryParts:
    """Build WHERE / state-lateral / cap-CTE / ORDER BY for a view spec.

    Pure — runs no DB execution. Each endpoint composes these into its own
    COUNT and SELECT (adding its own SELECTed columns + any endpoint-
    specific laterals like ``recent_ps``).
    """
    where_clauses: list[Any] = []
    # Stale-posting filter (Bestiary 5.18): hide closed by default.
    if not spec.include_closed:
        where_clauses.append(JobPosting.closed_at.is_(None))
    # Hard-rule filter (PR C): hide rows that failed a hard rule by default.
    if not spec.include_filtered:
        where_clauses.append(JobPosting.hard_rule_failed.is_(None))
    if spec.tier:
        where_clauses.append(TargetCompany.tier.in_(spec.tier))
    if spec.remote_type:
        where_clauses.append(JobPosting.remote_type.in_(spec.remote_type))
    if spec.role_family:
        # Case-insensitive match. role_family is a PG enum — cast to text
        # before lower().
        lowered = [v.lower() for v in spec.role_family]
        where_clauses.append(func.lower(cast(JobPosting.role_family, Text)).in_(lowered))
    if spec.target_company_id is not None:
        where_clauses.append(JobPosting.target_company_id == spec.target_company_id)
    if spec.ats:
        ats_exists = (
            select(PostingSource.id)
            .where(PostingSource.job_posting_id == JobPosting.id)
            .where(PostingSource.ats.in_(spec.ats))
            .exists()
        )
        where_clauses.append(ats_exists)

    recent_pa = latest_action_lateral()
    needs_state_lateral = bool(spec.state)

    if spec.state:
        state_clauses: list[Any] = []
        for s in spec.state:
            if s == "triage":
                state_clauses.append(
                    or_(
                        recent_pa.c.pa_action_type.is_(None),
                        recent_pa.c.pa_action_type == "reset",
                    )
                )
            elif s == "snoozed" and spec.include_snoozed_past_only:
                seven_days_ago = func.now() - timedelta(days=7)
                state_clauses.append(
                    and_(
                        recent_pa.c.pa_action_type == "snoozed",
                        or_(
                            recent_pa.c.pa_snooze_until < func.now(),
                            and_(
                                recent_pa.c.pa_snooze_until.is_(None),
                                recent_pa.c.pa_created_at < seven_days_ago,
                            ),
                        ),
                    )
                )
            elif s == "rejected":
                # feat/manual-application-status: the Rejected tab is now
                # ``resolved_status == 'rejected'`` (see resolved_status_expr).
                # Manual ``application_state.status='rejected'`` is authoritative;
                # the company-level Gmail rejection (PR #50) survives as the
                # fallback for untouched roles — folded into the resolved-status
                # CASE so an applied-but-unresolved card with a Gmail rejection
                # stays in Applied (Gmail is a hint there), not here.
                #
                # Asymmetry contract preserved: this predicate runs ONLY when the
                # operator explicitly asks for ``state=rejected``. Default Triage
                # never runs a rejection check, so a rejection at one role never
                # blunt-hides OTHER open roles at the same company.
                state_clauses.append(resolved_status_expr(recent_pa) == "rejected")
            elif s == "applied":
                # feat/manual-application-status: the Applied tab is now
                # ``resolved_status IN (applied, interview, offer)`` — applied
                # AND not terminal. Entry into Applied is unchanged (manual
                # posting_action=applied OR company-level Gmail
                # application_confirmation, both folded into resolved_status);
                # the manual lifecycle status governs whether a card STAYS or
                # moves. Marking accepted/rejected drops the card out of Applied
                # (resolved_status leaves the set) — this solves the removal
                # problem. Default Triage is unaffected (this predicate only runs
                # for state=applied).
                state_clauses.append(resolved_status_expr(recent_pa).in_(_APPLIED_TAB_STATUSES))
            else:
                state_clauses.append(recent_pa.c.pa_action_type == s)
        where_clauses.append(or_(*state_clauses) if state_clauses else sa_false())

    base_join = JobPosting.__table__.outerjoin(
        TargetCompany.__table__,
        JobPosting.target_company_id == TargetCompany.id,
    )

    # PR #58: per-company cap CTE. Ranking inside each company bucket is
    # FIXED to score → first_seen → id ASC regardless of the outer sort;
    # the outer sort then orders the cap-survivors. So
    # ``sort=oldest&per_company_cap=3`` returns "oldest of each company's
    # top-3 by score", not "oldest 3 per company".
    # feat/tunable-per-company-cap: three cap modes —
    #   * explicit 0           → cap disabled (capped_ids stays None, no CTE)
    #   * explicit int N > 0    → ``rn <= N`` (literal; the original behavior)
    #   * None (profile default)→ ``rn <= COALESCE((SELECT per_company_cap
    #                             FROM operator_profile WHERE id=1), 3)`` with
    #                             0 = unlimited. The scalar subquery INLINES
    #                             into the COUNT/SELECT statements, so the
    #                             operator's persisted cap is honored with NO
    #                             extra round-trip (2-query budget preserved).
    capped_ids: Select[Any] | None = None
    explicit_disabled = spec.per_company_cap == 0
    if not explicit_disabled:
        ranked_from = base_join.outerjoin(recent_pa, true()) if spec.state else base_join
        ranked_select = select(
            JobPosting.id.label("posting_id"),
            func.row_number()
            .over(
                partition_by=func.coalesce(
                    cast(JobPosting.target_company_id, Text),
                    cast(JobPosting.id, Text),
                ),
                order_by=[
                    JobPosting.fit_score.desc().nulls_last(),
                    JobPosting.first_seen_at.desc(),
                    JobPosting.id.asc(),
                ],
            )
            .label("rn"),
        ).select_from(ranked_from)
        for clause in where_clauses:
            ranked_select = ranked_select.where(clause)
        ranked_cte = ranked_select.cte("ranked_postings")
        if spec.per_company_cap is None:
            # Resolve the operator's persisted cap inline; COALESCE to 3 when
            # the singleton is unseeded; 0 in the column means "unlimited".
            cap_expr = func.coalesce(
                select(OperatorProfile.per_company_cap)
                .where(OperatorProfile.id == 1)
                .scalar_subquery(),
                3,
            )
            capped_ids = select(ranked_cte.c.posting_id).where(
                or_(cap_expr == 0, ranked_cte.c.rn <= cap_expr)
            )
        else:
            capped_ids = select(ranked_cte.c.posting_id).where(
                ranked_cte.c.rn <= spec.per_company_cap
            )

    # ORDER BY — ``JobPosting.id ASC`` is the stable tiebreaker on every
    # key. The Literal-typed FastAPI param guarantees the mapping is
    # exhaustive by construction.
    order_clauses: list[Any]
    if spec.sort == "newest":
        order_clauses = [JobPosting.first_seen_at.desc(), JobPosting.id.asc()]
    elif spec.sort == "oldest":
        order_clauses = [JobPosting.first_seen_at.asc(), JobPosting.id.asc()]
    elif spec.sort == "salary_high_to_low":
        order_clauses = [JobPosting.salary_max.desc().nulls_last(), JobPosting.id.asc()]
    elif spec.sort == "tier":
        order_clauses = [TargetCompany.tier.asc().nulls_last(), JobPosting.id.asc()]
    elif spec.sort == "recently_posted":
        order_clauses = [JobPosting.posted_at.desc().nulls_last(), JobPosting.id.asc()]
    elif spec.sort == "best_fit_semantic":
        # Slice 2b: blend the heuristic fit_score with the calibrated semantic
        # similarity_score behind the operator's similarity_weight (0 = off).
        # ``w`` is resolved inline from the singleton profile — same scalar-
        # subquery pattern as per_company_cap above — COALESCE'd to 0.0 when the
        # row is unseeded. COALESCE(similarity_score, fit_score) makes un-embedded
        # rows fall back to the heuristic, so at ANY w they rank exactly as
        # best_fit; at w=0 the whole expression collapses to fit_score, making
        # this byte-identical to best_fit. fit_score / score_posting are untouched.
        w = func.coalesce(
            select(OperatorProfile.similarity_weight)
            .where(OperatorProfile.id == 1)
            .scalar_subquery(),
            0.0,
        )
        blended = (1 - w) * JobPosting.fit_score + w * func.coalesce(
            JobPosting.similarity_score, JobPosting.fit_score
        )
        order_clauses = [blended.desc().nulls_last(), JobPosting.id.asc()]
    else:  # "best_fit"
        order_clauses = [JobPosting.fit_score.desc().nulls_last(), JobPosting.id.asc()]

    return PostingsQueryParts(
        spec=spec,
        base_join=base_join,
        where_clauses=where_clauses,
        recent_pa=recent_pa,
        needs_state_lateral=needs_state_lateral,
        capped_ids=capped_ids,
        order_clauses=order_clauses,
    )


__all__ = [
    "PostingsQueryParts",
    "PostingsViewSpec",
    "build_view_parts",
]
