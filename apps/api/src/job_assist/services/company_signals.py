"""Per-company application awareness (feat/company-app-awareness).

Surfaces what the Gmail outcome history already knows — at TRIAGE decision time —
so the operator sees "I have N live applications / M rejections at this company"
*before* investing in a role. Computed entirely from existing ``outcome_event``
data; no new ingestion.

This supersedes the original id-keyed ``compute_repeat_signals`` (#180). That
version keyed on ``target_company_id`` and so counted only the LINKED minority of
events — most ``outcome_event`` rows are unlinked (``target_company_id`` IS NULL),
and silently dropping them undercounts nearly every company. Here we attribute by
company NAME instead (linked ``target_company.name`` when present, else the name
extracted from the email subject — see ``company_name_match``), capturing the
unlinked majority.

Counts are company-level only — matched on the company NAME, never linked to a
specific posting (no fan-out). Keyed by the NORMALIZED name so "Stripe, Inc." and
"stripe" collapse. Ambiguous names (one a token-subset of another — "John
Hancock" vs "Manulife John Hancock") are SUPPRESSED entirely: a false count is
worse than no count.

The threshold UX (1-2 neutral, >=3 amber, 0 -> no badge) lives in the frontend;
this service returns every attributable company with ``rejections >= 1`` or
``active_apps >= 1`` and lets the UI decide styling.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import Contact, OutcomeEvent, TargetCompany
from job_assist.services.company_name_match import (
    ambiguous_keys,
    company_from_subject,
    normalize_company_name,
)

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


def _as_str(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


async def compute_repeat_signals(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Return per-company application-awareness counts, keyed by NORMALIZED
    company name::

        {norm_name: {"rejections": r, "active_apps": a, "contact_count": c,
                     "display_name": str}}

    * ``rejections`` — count of rejection ``outcome_event`` rows for the company.
    * ``active_apps`` — count of distinct still-alive applications. An
      "application" is one Gmail thread (``email_thread_id``; thread-less rows
      stand alone, matching the Pipeline's bucketing); its stage is its LATEST
      event's stage (latest-wins, so a rejection after a confirmation flips the
      thread out of "alive").
    * ``contact_count`` — feat/warm-path-badge: how many NON-ARCHIVED contacts
      list this company as ``current_employer`` (normalized the same way). The
      warm-path signal: alumni who can intro/refer the operator there.
    * ``display_name`` — the most common human-readable name seen for the key.

    Companies whose normalized key is ambiguous (a proper token-subset of another
    company's key) are omitted entirely — the guard runs over the UNION of
    outcome-derived and contact-derived keys, so a contact at "John Hancock" and
    outcomes at "Manulife John Hancock" suppress each other the same as two
    outcome sources would.
    """
    rows = (
        await session.execute(
            select(
                OutcomeEvent.email_thread_id,
                OutcomeEvent.id,
                OutcomeEvent.outcome_type,
                OutcomeEvent.received_at,
                OutcomeEvent.subject,
                TargetCompany.name.label("linked_name"),
            )
            .select_from(
                OutcomeEvent.__table__.outerjoin(
                    TargetCompany.__table__,
                    OutcomeEvent.target_company_id == TargetCompany.id,
                )
            )
            .where(OutcomeEvent.outcome_type.not_in(tuple(_NOISE_TYPES)))
        )
    ).all()

    rejections: Counter[str] = Counter()
    # (norm_key, thread_key) -> (received_at, outcome_type) of the latest event.
    latest: dict[tuple[str, str], tuple[Any, str]] = {}
    # norm_key -> Counter of raw display names (pick the most common for the UI).
    display_names: dict[str, Counter[str]] = {}

    for thread_id, oid, outcome_type, received_at, subject, linked_name in rows:
        raw_name = linked_name or company_from_subject(subject)
        if not raw_name:
            continue  # cannot attribute to a company → skip (no fan-out)
        key = normalize_company_name(raw_name)
        if not key:
            continue  # vendor/empty → not a real company

        display_names.setdefault(key, Counter())[raw_name.strip()] += 1

        otype = _as_str(outcome_type)
        if otype in _REJECTION_TYPES:
            rejections[key] += 1
        thread_key = thread_id or f"o:{oid}"
        lk = (key, thread_key)
        current = latest.get(lk)
        if current is None or received_at > current[0]:
            latest[lk] = (received_at, otype)

    active: Counter[str] = Counter()
    for (key, _thread), (_received_at, otype) in latest.items():
        if otype in _ALIVE_TYPES:
            active[key] += 1

    # ── Warm-path contacts (feat/warm-path-badge) ────────────────────────
    # Non-archived contacts grouped by normalized current_employer. Freeform
    # operator/alumni-directory text, so it goes through the same normalizer
    # as the outcome names ("John Deere" / "john deere " collapse).
    contact_rows = (
        await session.execute(
            select(Contact.current_employer)
            .where(Contact.archived_at.is_(None))
            .where(Contact.current_employer.is_not(None))
        )
    ).scalars()
    contacts: Counter[str] = Counter()
    for employer in contact_rows:
        raw = (employer or "").strip()
        key = normalize_company_name(raw)
        if not key:
            continue
        contacts[key] += 1
        display_names.setdefault(key, Counter())[raw] += 1

    candidate_keys = set(rejections) | set(active) | set(contacts)
    # No-false-badge guard: drop any key whose tokens are a subset/superset of
    # another's — we can't safely attribute the shorter name. Runs over the
    # union, so contact-derived and outcome-derived names guard each other.
    suppressed = ambiguous_keys(candidate_keys)

    signals: dict[str, dict[str, Any]] = {}
    for key in candidate_keys:
        if key in suppressed:
            continue
        r = rejections.get(key, 0)
        a = active.get(key, 0)
        c = contacts.get(key, 0)
        if r < 1 and a < 1 and c < 1:
            continue
        names = display_names.get(key)
        display = names.most_common(1)[0][0] if names else key
        signals[key] = {
            "rejections": r,
            "active_apps": a,
            "contact_count": c,
            "display_name": display,
        }
    return signals


__all__ = ["compute_repeat_signals"]
