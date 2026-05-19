"""Aggregation queries behind ``/stats/calibration`` and ``/stats/funnel`` (PR #30b).

Stage-counting rules (mirrored across both endpoints, kept in one place
to avoid drift):

  SURFACED        — ``job_posting.first_seen_at`` within window
  INTERESTED      — any ``posting_action`` in window with action_type
                    IN ('interested','applied'); ``applied`` counts toward
                    INTERESTED because skipping straight to applied still
                    implies prior interest
  APPLIED         — any ``posting_action`` in window with
                    action_type='applied'
  REJECTED_BY_YOU — any ``posting_action`` in window with
                    action_type='not_interested'

Each posting counts at most once per stage regardless of action count
— that's the ``COUNT(DISTINCT job_posting_id)`` everywhere.

Query budget:
  calibration  : 2 queries (1 multi-FILTER aggregation + 1 top-families GROUP BY)
  funnel       : 1 query  (multi-FILTER aggregation)

The window is *passed in already-validated* — the endpoint layer calls
``validate_window`` before invoking either function here, so this module
can trust the bounds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import JobPosting, PostingAction

# ── Helpers ──────────────────────────────────────────────────────────────────


def _round2(value: float) -> float:
    """Round to 2dp. Centralised so the rounding policy is unambiguous."""
    return round(value, 2)


def _safe_rate(num: int, denom: int) -> float | None:
    """Return rate rounded to 2dp, or ``None`` when the denominator is zero.

    The spec is explicit that an empty upstream stage means we surface
    ``null`` rather than 0 — the frontend renders "—" in that case.
    """
    if denom == 0:
        return None
    return _round2(num / denom)


# ── Calibration ──────────────────────────────────────────────────────────────


async def get_calibration(
    session: AsyncSession,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Return the Calibration card payload.

    Query 1 — one row with the four scalar counts. The ``SURFACED`` count
    sits on the ``job_posting`` row (not on an action), so we use
    LEFT JOIN posting_action and conditional aggregates: the surfaced
    FILTER looks at job_posting columns; the action FILTERs look at the
    action row. ``DISTINCT job_posting_id`` everywhere ensures a posting
    that bounced interested→applied→reset still counts at most once per
    stage.

    Query 2 — top rejected role families, joined on the same window.
    Done separately because it's a different cardinality (a row per
    role_family, not a single aggregate row).
    """
    in_window_action = (PostingAction.created_at >= since) & (PostingAction.created_at <= until)
    in_window_surfaced = (JobPosting.first_seen_at >= since) & (JobPosting.first_seen_at <= until)

    # The single-row counts query. LEFT JOIN so postings with no actions
    # still contribute to SURFACED.
    counts_stmt = select(
        func.count(func.distinct(JobPosting.id)).filter(in_window_surfaced).label("surfaced"),
        func.count(func.distinct(JobPosting.id))
        .filter(in_window_action & PostingAction.action_type.in_(("interested", "applied")))
        .label("interested"),
        func.count(func.distinct(JobPosting.id))
        .filter(in_window_action & (PostingAction.action_type == "applied"))
        .label("applied"),
        func.count(func.distinct(JobPosting.id))
        .filter(in_window_action & (PostingAction.action_type == "not_interested"))
        .label("rejected_by_you"),
    ).select_from(
        JobPosting.__table__.outerjoin(
            PostingAction.__table__,
            PostingAction.job_posting_id == JobPosting.id,
        )
    )

    counts_row = (await session.execute(counts_stmt)).one()
    surfaced = int(counts_row.surfaced or 0)
    interested = int(counts_row.interested or 0)
    applied = int(counts_row.applied or 0)
    rejected = int(counts_row.rejected_by_you or 0)

    # Top rejected role families, ordered (count DESC, name ASC) to
    # break ties deterministically. NULL role_family is filtered out at
    # the WHERE level so it neither competes for nor wastes a top-5 slot.
    # NB: label the count "n" — Row.count is a tuple method, and naming
    # the label "count" makes mypy think attribute access returns the
    # bound method rather than the SQL scalar.
    top_stmt = (
        select(
            JobPosting.role_family.label("role_family"),
            func.count(func.distinct(JobPosting.id)).label("n"),
        )
        .select_from(
            JobPosting.__table__.join(
                PostingAction.__table__,
                PostingAction.job_posting_id == JobPosting.id,
            )
        )
        .where(in_window_action)
        .where(PostingAction.action_type == "not_interested")
        .where(JobPosting.role_family.is_not(None))
        .group_by(JobPosting.role_family)
        .order_by(func.count(func.distinct(JobPosting.id)).desc(), JobPosting.role_family.asc())
        .limit(5)
    )
    top_rows = (await session.execute(top_stmt)).all()
    top_rejected = [
        {
            "role_family": str(getattr(r.role_family, "value", r.role_family)),
            "count": int(r.n),
        }
        for r in top_rows
    ]

    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "surfaced": surfaced,
        "interested": interested,
        "interested_rate": _safe_rate(interested, surfaced),
        "applied": applied,
        "rejected_by_you": rejected,
        "top_rejected_role_families": top_rejected,
    }


# ── Funnel ───────────────────────────────────────────────────────────────────


_FUNNEL_STAGE_ORDER = ("surfaced", "interested", "applied")


async def get_funnel(
    session: AsyncSession,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Return the funnel payload.

    Stages are always returned in the fixed order
    ``[surfaced, interested, applied]``; conversion rates are computed
    pairwise across adjacent stages with ``None`` whenever the upstream
    stage count is 0. One aggregation query, same LEFT-JOIN pattern as
    calibration.
    """
    in_window_action = (PostingAction.created_at >= since) & (PostingAction.created_at <= until)
    in_window_surfaced = (JobPosting.first_seen_at >= since) & (JobPosting.first_seen_at <= until)

    stmt = select(
        func.count(func.distinct(JobPosting.id)).filter(in_window_surfaced).label("surfaced"),
        func.count(func.distinct(JobPosting.id))
        .filter(in_window_action & PostingAction.action_type.in_(("interested", "applied")))
        .label("interested"),
        func.count(func.distinct(JobPosting.id))
        .filter(in_window_action & (PostingAction.action_type == "applied"))
        .label("applied"),
    ).select_from(
        JobPosting.__table__.outerjoin(
            PostingAction.__table__,
            PostingAction.job_posting_id == JobPosting.id,
        )
    )
    row = (await session.execute(stmt)).one()

    counts = {
        "surfaced": int(row.surfaced or 0),
        "interested": int(row.interested or 0),
        "applied": int(row.applied or 0),
    }
    stages = [{"name": name, "count": counts[name]} for name in _FUNNEL_STAGE_ORDER]
    conversion_rates = [
        {
            "from": _FUNNEL_STAGE_ORDER[i],
            "to": _FUNNEL_STAGE_ORDER[i + 1],
            "rate": _safe_rate(
                counts[_FUNNEL_STAGE_ORDER[i + 1]],
                counts[_FUNNEL_STAGE_ORDER[i]],
            ),
        }
        for i in range(len(_FUNNEL_STAGE_ORDER) - 1)
    ]
    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "stages": stages,
        "conversion_rates": conversion_rates,
    }
