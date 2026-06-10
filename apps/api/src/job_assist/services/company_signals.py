"""Per-company repeat signals (feat/repeat-signal-flags).

Surfaces what's already known from the Gmail outcome history: companies where
the operator has been rejected multiple times, or has multiple still-alive
applications. Both are computed from ``outcome_event`` — no new data — and keyed
by ``target_company_id`` (the reliable link set by domain match; the unlinked
majority simply doesn't carry a company, so it can't and shouldn't be flagged).

A signal is emitted for a company only when it crosses the threshold on either
axis (``_MIN_REPEAT``), so the frontend can badge "N rejections here" /
"N active apps here" wherever that company's roles appear (Triage detail +
Pipeline).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import OutcomeEvent

# A rejection outcome (mirrors postings_query._REJECTION_OUTCOME_TYPES).
_REJECTION_TYPES = frozenset(
    {"rejection_pre_screen", "rejection_post_screen", "rejection_post_interview"}
)

# "Still-alive" = an application whose LATEST event maps to a non-terminal
# pipeline stage. Mirrors lib/applied/stages.ts ``stageOf`` (everything that
# isn't a rejection / withdrawn / noise). Kept in lockstep with that file.
_ALIVE_TYPES = frozenset(
    {
        "application_confirmation",
        "recruiter_screen_invite",
        "phone_interview_invite",
        "video_interview_invite",
        "onsite_interview_invite",
        "panel_interview_invite",
        "offer",
    }
)

# The classifier's non-job noise buckets — never counted.
_NOISE_TYPES = frozenset({"unrelated", "unclassified"})

# Threshold: "MULTIPLE" means two or more.
_MIN_REPEAT = 2


def _as_str(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


async def compute_repeat_signals(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Return ``{company_id: {"rejections": r, "active_apps": a}}`` for every
    company at/above the repeat threshold on either axis.

    * ``rejections`` — count of rejection ``outcome_event`` rows for the company.
    * ``active_apps`` — count of distinct still-alive applications. An
      "application" is one Gmail thread (``email_thread_id``; thread-less rows
      stand alone, matching the Pipeline's bucketing); its stage is its LATEST
      event's stage (latest-wins, so a rejection after a confirmation flips the
      thread out of "alive").
    """
    rows = (
        await session.execute(
            select(
                OutcomeEvent.target_company_id,
                OutcomeEvent.email_thread_id,
                OutcomeEvent.id,
                OutcomeEvent.outcome_type,
                OutcomeEvent.received_at,
            )
            .where(OutcomeEvent.target_company_id.is_not(None))
            .where(OutcomeEvent.outcome_type.not_in(tuple(_NOISE_TYPES)))
        )
    ).all()

    rejections: Counter[str] = Counter()
    # (company_id, thread_key) -> (received_at, outcome_type) of the latest event.
    latest: dict[tuple[str, str], tuple[Any, str]] = {}

    for company_id, thread_id, oid, outcome_type, received_at in rows:
        cid = str(company_id)
        otype = _as_str(outcome_type)
        if otype in _REJECTION_TYPES:
            rejections[cid] += 1
        thread_key = thread_id or f"o:{oid}"
        key = (cid, thread_key)
        current = latest.get(key)
        if current is None or received_at > current[0]:
            latest[key] = (received_at, otype)

    active: Counter[str] = Counter()
    for (cid, _thread), (_received_at, otype) in latest.items():
        if otype in _ALIVE_TYPES:
            active[cid] += 1

    signals: dict[str, dict[str, int]] = {}
    for cid in set(rejections) | set(active):
        r = rejections.get(cid, 0)
        a = active.get(cid, 0)
        if r >= _MIN_REPEAT or a >= _MIN_REPEAT:
            signals[cid] = {"rejections": r, "active_apps": a}
    return signals


__all__ = ["compute_repeat_signals"]
