"""Applied-company tracking (feat/applied-company-tracking).

The operator applies broadly and finds those roles via LinkedIn/network, so
re-ingesting is redundant. The value here is purely that the Companies view
should REFLECT real application activity — without ever pulling these
companies into ingestion.

``sync_applied_companies`` scans ``application_confirmation`` outcomes, resolves
a company name (existing link → subject extraction → skip; NEVER from_domain,
which is the ATS vendor), and:
  * matches an EXISTING target_company (curated / broad / prior tracking) →
    links the outcomes (sets ``target_company_id``), never duplicates;
  * no match + count >= threshold → creates a tracking-only ``source='applied'``
    row (``ats=unknown``, ``ats_handle=NULL``, ``tier=NULL``) and links;
  * no match + below threshold (a one-off, often a mis-extraction) → reported
    in ``suggested`` WITHOUT committing.

Tracking rows are excluded from ingestion by construction: ``tier IS NULL`` and
``ats_handle IS NULL`` already fail the daily-cron plan, and the plan now also
filters ``source != 'applied'`` explicitly. Counts are NOT denormalised — the
linked ``outcome_event`` rows are the single source of truth (``/companies``
derives ``application_count`` / ``last_applied_at`` live).

Mirrors ``outcome_relink.relink_unmatched``: deterministic order, commit every
~25 writes. Reuses ``_normalize_company`` + ``_match_target_company`` from the
Gmail backfill so name matching is identical across the two linkers.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.enums import ATS
from job_assist.db.models import OutcomeEvent, TargetCompany
from job_assist.gmail.backfill import _match_target_company, _normalize_company

logger = logging.getLogger(__name__)

# Default: a company must appear in >= 2 application_confirmations before we
# auto-create a net-new tracking row. One-off extractions are the highest
# mis-naming risk, so they're surfaced as suggestions instead of committed.
DEFAULT_THRESHOLD = 2

# ── Subject → company extractor (Python port of lib/pipeline/companyFromSubject) ─
# Anchored on the apply verb; "for" excluded ("applying for the <role>" is the
# role, not the company). Captures the trailing remainder for _clean_company.
_APPLY_RE = re.compile(r"appl(?:ying|ication)\s+(?:to|at|with)\s+(.+)$", re.IGNORECASE)
# The apostrophe (curly U+2019) and en/em dashes are injected via chr() so the
# source stays ASCII-only (ruff RUF001) while the compiled patterns still match
# those characters as they appear in real Gmail subjects.
_RSQUO = chr(0x2019)
_EN, _EM = chr(0x2013), chr(0x2014)
_POSSESSIVE_RE = re.compile(r"^(.+?)['" + _RSQUO + r"`]s\s+\S+")
_SEPARATOR_RE = re.compile(r"\s+[-" + _EN + _EM + r"|:]\s+")
_FOR_TAIL_RE = re.compile(r"\s+for\s+(?:the\s+|our\s+|a\s+)?.*$", re.IGNORECASE)
_ROLE_TAIL_RE = re.compile(
    r"\s+(position|role|opening|opportunity|req(?:uisition)?)\b.*$", re.IGNORECASE
)
_TRAILING_PUNCT_RE = re.compile(r"[\s!.,;:" + _EN + _EM + r"-]+$")
_LEADING_THE_RE = re.compile(r"^the\s+", re.IGNORECASE)


def company_from_subject(subject: str | None) -> str | None:
    """Derive a company name from an ATS confirmation/rejection subject.

    Returns the cleaned company, or ``None`` for a generic subject ("Update on
    Your Application") so the caller can skip it. Never returns a domain.
    """
    if not subject:
        return None
    s = subject.strip()
    if not s:
        return None
    m = _APPLY_RE.search(s)
    candidate: str | None = m.group(1) if m else None
    if candidate is None:
        poss = _POSSESSIVE_RE.match(s)
        if poss:
            candidate = poss.group(1)
    if candidate is None:
        return None
    return _clean_company(candidate)


def _clean_company(raw: str) -> str | None:
    c = raw.strip()
    c = _SEPARATOR_RE.split(c)[0].strip()
    c = _FOR_TAIL_RE.sub("", c)
    c = _ROLE_TAIL_RE.sub("", c)
    c = _TRAILING_PUNCT_RE.sub("", c).strip()
    c = _LEADING_THE_RE.sub("", c).strip()
    return c or None


class AppliedSyncReport(BaseModel):
    """Counters returned by ``sync_applied_companies`` for the admin endpoint."""

    scanned: int = 0
    created: int = 0  # net-new source='applied' tracking rows
    linked: int = 0  # outcome_event rows that got a target_company_id
    skipped_unnamed: int = 0  # subject extraction yielded nothing
    suggested: list[dict[str, object]] = []  # [{name, count}] below threshold


async def sync_applied_companies(
    session: AsyncSession,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    limit: int | None = None,
) -> AppliedSyncReport:
    """Upsert tracking-only companies from application_confirmation outcomes.

    Idempotent: only UNLINKED rows are considered (``target_company_id IS
    NULL``); re-runs match previously-created tracking rows by normalised name
    and link rather than duplicate.
    """
    report = AppliedSyncReport()

    query = (
        select(OutcomeEvent)
        .where(OutcomeEvent.target_company_id.is_(None))
        .where(OutcomeEvent.outcome_type == "application_confirmation")
        .order_by(OutcomeEvent.received_at.asc(), OutcomeEvent.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    rows = (await session.execute(query)).scalars().all()
    report.scanned = len(rows)

    # Group unlinked rows by normalised company key. Each group carries the
    # first usable display name + the events to link.
    groups: dict[str, dict[str, object]] = {}
    for event in rows:
        name = company_from_subject(event.subject)
        if not name:
            report.skipped_unnamed += 1
            continue
        key = _normalize_company(name)
        if not key:
            report.skipped_unnamed += 1
            continue
        grp = groups.get(key)
        if grp is None:
            groups[key] = {"name": name, "events": [event]}
        else:
            events = grp["events"]
            assert isinstance(events, list)
            events.append(event)

    writes_since_commit = 0
    for grp in groups.values():
        display_name = grp["name"]
        events = grp["events"]
        assert isinstance(display_name, str)
        assert isinstance(events, list)
        count = len(events)

        # Match an existing company by name only — from_domain is the ATS
        # vendor, useless (and dangerous) as a match key here.
        tc = await _match_target_company(session, from_domain="", extracted_company=display_name)

        if tc is None:
            if count < threshold:
                report.suggested.append({"name": display_name, "count": count})
                continue
            tc = TargetCompany(
                name=display_name,
                ats=ATS.unknown,
                ats_handle=None,
                tier=None,
                source="applied",
            )
            session.add(tc)
            await session.flush()  # assign tc.id for the FK below
            report.created += 1

        # Capture the id as a plain value BEFORE the link loop. ``session.commit``
        # below expires every ORM object (expire_on_commit default), so reading
        # ``tc.id`` after a commit would trigger an implicit IO refresh outside
        # the async greenlet → MissingGreenlet. Setting the (also-expired)
        # ``event`` attribute is safe; the next flush refreshes within context.
        tc_id = tc.id
        for event in events:
            event.target_company_id = tc_id
            report.linked += 1
            writes_since_commit += 1
            if writes_since_commit >= 25:
                await session.commit()
                writes_since_commit = 0

    if writes_since_commit > 0:
        await session.commit()

    logger.info(
        "applied_companies.sync_complete",
        extra={
            "scanned": report.scanned,
            "created": report.created,
            "linked": report.linked,
            "suggested": len(report.suggested),
            "skipped_unnamed": report.skipped_unnamed,
        },
    )
    return report


__all__ = ["AppliedSyncReport", "company_from_subject", "sync_applied_companies"]
