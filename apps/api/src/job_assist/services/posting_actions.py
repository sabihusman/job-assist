"""Operator-action service layer (PR #31).

Single source of truth for *validating and persisting* operator
decisions on a job posting. The endpoint layer in ``main.py`` is a thin
wrapper that translates :class:`ValueError` to HTTP 422 and
:class:`LookupError` to 404 — all the business rules live here.

Why a service module rather than inline-in-route logic?
* The DB CHECK constraints encode the same rules at the storage layer,
  so any service-level bug surfaces as an IntegrityError; the test suite
  asserts both paths.
* The "latest action per posting" lookup uses a non-trivial SQL pattern
  (LATERAL on Postgres) that's worth reusing from both the list and
  detail endpoints.
* Future callers (CLI, batch jobs) can hit this without rebuilding the
  validation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from job_assist.db.enums import ActionReason, ActionType
from job_assist.db.models import JobPosting, PostingAction


@dataclass(frozen=True, slots=True)
class CurrentState:
    """Snapshot of the latest action on a posting.

    Returned by :func:`get_current_state` and :func:`bulk_get_current_states`.
    ``None`` (not an instance of this class) is used when no actions
    exist for a posting — distinct from a row whose action_type is
    ``reset`` (which IS a CurrentState with action_type=reset).
    """

    action_type: ActionType
    reason: ActionReason | None
    snooze_until: datetime | None
    created_at: datetime


# ── Validation helpers ────────────────────────────────────────────────────────


def _validate(
    action_type: ActionType,
    reason: ActionReason | None,
    snooze_until: datetime | None,
) -> None:
    """Mirror of the CHECK constraints, raised as ValueError for HTTP 422.

    Doing this in Python first gives the client a clean error message
    ("reason required when action_type='not_interested'") rather than
    PG's opaque "violates check constraint ck_posting_action_..." text.
    The DB layer still enforces — see test_db_check_constraint_* tests.
    """
    if action_type == ActionType.not_interested and reason is None:
        raise ValueError(
            "reason is required when action_type='not_interested'",
        )
    if action_type != ActionType.not_interested and reason is not None:
        raise ValueError(
            f"reason must be null when action_type={action_type.value!r} "
            "(reasons are only meaningful for 'not_interested')",
        )
    if snooze_until is not None and action_type != ActionType.snoozed:
        raise ValueError(
            f"snooze_until is only valid when action_type='snoozed' "
            f"(got action_type={action_type.value!r})",
        )
    if snooze_until is not None:
        # Normalise naive-utc to aware so comparisons don't blow up on
        # the "can't compare naive and aware" TypeError.
        ts = snooze_until if snooze_until.tzinfo else snooze_until.replace(tzinfo=UTC)
        if ts <= datetime.now(tz=UTC):
            raise ValueError("snooze_until must be in the future")


async def _assert_posting_exists(session: AsyncSession, job_posting_id: uuid.UUID) -> None:
    """Raise LookupError if the posting doesn't exist.

    Done as a tiny scalar SELECT rather than relying on the FK insert
    failure so the caller gets a clean 404 (LookupError) instead of an
    IntegrityError surfaced as 500.
    """
    exists = (
        await session.execute(
            select(literal(1)).where(JobPosting.id == job_posting_id).limit(1),
        )
    ).scalar_one_or_none()
    if exists is None:
        raise LookupError(f"job_posting {job_posting_id} not found")


# ── Write path ───────────────────────────────────────────────────────────────


async def record_action(
    session: AsyncSession,
    job_posting_id: uuid.UUID,
    action_type: ActionType,
    reason: ActionReason | None = None,
    snooze_until: datetime | None = None,
    notes: str | None = None,
    resume_version_id: uuid.UUID | None = None,
) -> PostingAction:
    """Insert one ``posting_action`` row.

    ``resume_version_id`` (feat/resume-version-tracking) tags which
    tailored resume was sent with an application. It's only valid on an
    ``applied`` action; supplying it for any other action_type raises
    ValueError (mirrors the DB CHECK). The FK existence is enforced by
    the DB (a bad id surfaces as an IntegrityError); we validate the
    cross-field rule here for a clean 422.

    Raises:
        ValueError: any cross-field rule failed (becomes HTTP 422).
        LookupError: ``job_posting_id`` doesn't exist (becomes HTTP 404).
    """
    _validate(action_type, reason, snooze_until)
    if resume_version_id is not None and action_type != ActionType.applied:
        raise ValueError("resume_version_id is only valid with action_type='applied'")
    await _assert_posting_exists(session, job_posting_id)

    row = PostingAction(
        job_posting_id=job_posting_id,
        action_type=action_type.value,
        reason=reason.value if reason else None,
        snooze_until=snooze_until,
        notes=notes,
        resume_version_id=resume_version_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# ── Read path ────────────────────────────────────────────────────────────────


def _row_to_state(row: PostingAction) -> CurrentState:
    return CurrentState(
        action_type=ActionType(row.action_type),
        reason=ActionReason(row.reason) if row.reason else None,
        snooze_until=row.snooze_until,
        created_at=row.created_at,
    )


async def get_current_state(
    session: AsyncSession,
    job_posting_id: uuid.UUID,
) -> CurrentState | None:
    """Return the latest action for a posting, or None if the operator hasn't acted."""
    row = (
        await session.execute(
            select(PostingAction)
            .where(PostingAction.job_posting_id == job_posting_id)
            .order_by(PostingAction.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return _row_to_state(row) if row else None


async def bulk_get_current_states(
    session: AsyncSession,
    job_posting_ids: list[uuid.UUID],
) -> dict[uuid.UUID, CurrentState | None]:
    """Return latest action per posting in **one** SQL query.

    Uses ``DISTINCT ON (job_posting_id) ... ORDER BY job_posting_id,
    created_at DESC`` — Postgres-specific but the project is Postgres-only.
    Returns a dict keyed by every input id; missing ids map to None.

    The empty-list short-circuit avoids issuing an ``IN ()`` that
    SQLAlchemy 2.0 emits as ``IN (NULL)`` with a deprecation warning.
    """
    out: dict[uuid.UUID, CurrentState | None] = dict.fromkeys(job_posting_ids)
    if not job_posting_ids:
        return out

    stmt = (
        select(PostingAction)
        .where(PostingAction.job_posting_id.in_(job_posting_ids))
        .distinct(PostingAction.job_posting_id)
        .order_by(
            PostingAction.job_posting_id,
            PostingAction.created_at.desc(),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        out[row.job_posting_id] = _row_to_state(row)
    return out


# ── Lateral subquery for list endpoints ──────────────────────────────────────


def latest_action_lateral() -> Any:
    """Build a LATERAL subquery yielding the most-recent posting_action.

    Used by ``GET /postings`` to fold the state JOIN into the main SELECT
    instead of issuing a separate ``bulk_get_current_states`` call.
    Returns a tuple ``(lateral, columns)`` where columns is a dict of
    handles the caller can reference in the outer SELECT.
    """
    pa_alias = aliased(PostingAction)
    lateral = (
        select(
            pa_alias.action_type.label("pa_action_type"),
            pa_alias.reason.label("pa_reason"),
            pa_alias.snooze_until.label("pa_snooze_until"),
            pa_alias.created_at.label("pa_created_at"),
        )
        .where(pa_alias.job_posting_id == JobPosting.id)
        .order_by(pa_alias.created_at.desc())
        .limit(1)
        .lateral("recent_pa")
    )
    return lateral
