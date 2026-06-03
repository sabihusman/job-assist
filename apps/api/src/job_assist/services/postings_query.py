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

from sqlalchemy import Text, and_, cast, func, or_, select, true
from sqlalchemy import false as sa_false
from sqlalchemy.sql import Select

from job_assist.db.models import (
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
                # Bestiary (PR #50): cross-table state. EXISTS folds in
                # without a join — no LATERAL added.
                #
                # feat/surface-linked-outcomes: re-pointed from
                # ``OutcomeEvent.job_posting_id == JobPosting.id`` to
                # ``OutcomeEvent.target_company_id ==
                # JobPosting.target_company_id``. The job_posting_id
                # column is deferred-by-design (gmail/backfill.py:9-14)
                # and uniformly NULL in production, so the old predicate
                # matched zero rows. Company-level is the link that
                # actually fires today.
                #
                # Asymmetry contract: this predicate runs ONLY when the
                # operator explicitly asks for ``state=rejected``. The
                # default Triage view (``state=triage``) does NOT include
                # any rejection check, so a rejection at one role at a
                # company never blunt-hides OTHER open roles at the same
                # company from the default queue. The Rejected view is
                # the opt-in surface; default Triage is unaffected.
                #
                # Defensive: when ``JobPosting.target_company_id IS NULL``
                # the equality is NULL=… → NULL (untrue) under SQL three-
                # valued logic, so a NULL posting never matches even if
                # outcome_event has a row with NULL target. Belt + braces.
                rejected_exists = (
                    select(OutcomeEvent.id)
                    .where(JobPosting.target_company_id.is_not(None))
                    .where(OutcomeEvent.target_company_id == JobPosting.target_company_id)
                    .where(OutcomeEvent.outcome_type.in_(_REJECTION_OUTCOME_TYPES))
                    .exists()
                )
                state_clauses.append(rejected_exists)
            elif s == "applied":
                # feat/surface-linked-outcomes: union the operator's
                # manual ``posting_action.action_type='applied'`` (the
                # keyboard ``4`` key, per-posting) with the Gmail-derived
                # ``application_confirmation`` outcome at company level.
                #
                # Asymmetry contract (same as rejected): this predicate
                # runs ONLY when the operator explicitly asks for
                # ``state=applied`` (the Applied page hook). The default
                # Triage view is unaffected — an application_confirmation
                # linked at a company does NOT blunt-hide other open
                # roles at that company from default Triage. Manual ``4``
                # remains the only mechanism that hides a posting from
                # default Triage; Gmail signal only enriches the Applied
                # page.
                #
                # Why company-level for the EXISTS half: outcome_event
                # rows have ``job_posting_id`` uniformly NULL (deferred-
                # by-design per gmail/backfill.py:9-14). Company-level is
                # the only link that fires today. When per-posting outcome
                # linkage ships later, this can tighten to posting-level
                # for higher-precision Applied surfacing.
                applied_at_company_exists = (
                    select(OutcomeEvent.id)
                    .where(JobPosting.target_company_id.is_not(None))
                    .where(OutcomeEvent.target_company_id == JobPosting.target_company_id)
                    .where(OutcomeEvent.outcome_type == "application_confirmation")
                    .exists()
                )
                state_clauses.append(
                    or_(
                        recent_pa.c.pa_action_type == "applied",
                        applied_at_company_exists,
                    )
                )
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
