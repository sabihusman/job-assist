"""Link Gmail outcome_events to a SPECIFIC corpus posting (feat/applied-pipeline-crosslink).

Cross-link only — purely navigational. We populate ``outcome_event.job_posting_id``
so the Pipeline (Gmail) and the triage/Applied posting can reference each other.
This does NOT change any status and does NOT feed scoring or tab membership — the
posting-specific Applied/Rejected fix (no company-level fanout) is preserved
exactly. Manual ``application_state`` stays authoritative; the Gmail link is an
informational pointer.

THE NO-FANOUT RULE
──────────────────
``outcome_event.target_company_id`` already links an email to a company (set by
``outcome_relink``). A company can have MANY corpus postings (e.g. 10 Capital
One PM roles). Linking an email to *all* of them is the exact fanout bug we
fixed. So a link is created only when the email can be tied to ONE specific
posting by ROLE:

  1. Candidates = OPEN postings at the email's ``target_company_id``.
  2. Score each candidate by how much of its title's significant tokens appear
     in the email's subject + snippet (role recall).
  3. Link the single best candidate ONLY when it clears a score threshold AND a
     minimum number of matched role tokens AND (the company has exactly one
     candidate OR the best beats the runner-up by a margin).
  4. Otherwise leave ``job_posting_id`` NULL — the email stays a Gmail-only
     Pipeline entry (correct for the majority: applied directly, never triaged).

Idempotent: only rows with ``job_posting_id IS NULL`` are considered, so a
re-run never overwrites an existing link. Deterministic (pure token overlap, no
LLM). Run by ``POST /admin/outcomes/link-postings`` (one-shot backfill) and on
the gmail-poll tail for freshly-inserted outcomes.
"""

from __future__ import annotations

import logging
import re
import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import JobPosting, OutcomeEvent

logger = logging.getLogger(__name__)

# Outcome types worth linking — same job-related set the relinker uses.
_LINKABLE_TYPES: tuple[str, ...] = (
    "application_confirmation",
    "recruiter_screen_invite",
    "phone_interview_invite",
    "video_interview_invite",
    "onsite_interview_invite",
    "panel_interview_invite",
    "offer",
    "rejection_pre_screen",
    "rejection_post_screen",
    "rejection_post_interview",
    "withdrawn",
)

# Generic words that carry no role signal — present in nearly every title /
# confirmation email, so matching on them alone would mis-link. We require
# matched tokens OUTSIDE this set.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # email boilerplate
        "thank",
        "you",
        "for",
        "applying",
        "your",
        "application",
        "to",
        "the",
        "at",
        "we",
        "received",
        "interest",
        "in",
        "role",
        "position",
        "team",
        "a",
        "an",
        "of",
        "and",
        "has",
        "been",
        "is",
        "our",
        "with",
        "this",
        "re",
        "fwd",
        # over-generic role words (need a more specific token alongside)
        "manager",
        "senior",
        "sr",
        "staff",
        "lead",
        "principal",
        "associate",
        "junior",
        "jr",
        "i",
        "ii",
        "iii",
    }
)

# Tunables.
_MIN_SCORE = 0.6  # ≥60% of the title's significant tokens appear in the email
_MIN_MATCHED_TOKENS = 2  # at least 2 significant role tokens overlap
_MARGIN = 0.2  # best must beat runner-up by this when >1 candidate

_WORD_RE = re.compile(r"[a-z0-9]+")


class LinkReport(BaseModel):
    """Counters returned by ``link_outcomes_to_postings``."""

    scanned: int = 0
    linked: int = 0
    no_candidate: int = 0  # company has no open corpus posting
    ambiguous: int = 0  # candidates exist but none is a confident single match


def _significant_tokens(*texts: str | None) -> set[str]:
    """Token set from the given texts, minus stopwords / generic role words."""
    out: set[str] = set()
    for text in texts:
        if not text:
            continue
        for tok in _WORD_RE.findall(text.lower()):
            if tok not in _STOPWORDS and len(tok) > 1:
                out.add(tok)
    return out


def role_match_score(subject: str | None, snippet: str | None, title: str | None) -> float:
    """Recall of the posting title's significant tokens within the email text.

    ``|title_tokens ∩ email_tokens| / |title_tokens|`` over significant tokens
    only. 0.0 when the title has no significant tokens (can't disambiguate).
    Pure + deterministic so the matcher is testable without a DB.
    """
    title_tokens = _significant_tokens(title)
    if not title_tokens:
        return 0.0
    email_tokens = _significant_tokens(subject, snippet)
    matched = title_tokens & email_tokens
    if len(matched) < _MIN_MATCHED_TOKENS:
        return 0.0
    return len(matched) / len(title_tokens)


def _best_posting_id(
    event: OutcomeEvent,
    candidates: list[JobPosting],
) -> uuid.UUID | None:
    """Pick the single best-matching candidate posting, or None.

    Conservative: returns a posting id only when the top score clears
    ``_MIN_SCORE`` AND (there is exactly one candidate OR it beats the
    runner-up by ``_MARGIN``). This is what prevents a single email from
    fanning across a company's many postings.
    """
    if not candidates:
        return None
    scored = sorted(
        (
            (role_match_score(event.subject, event.raw_snippet, p.normalized_title), p)
            for p in candidates
        ),
        key=lambda t: t[0],
        reverse=True,
    )
    best_score, best_posting = scored[0]
    if best_score < _MIN_SCORE:
        return None
    if len(scored) > 1:
        second_score = scored[1][0]
        if best_score - second_score < _MARGIN:
            return None  # ambiguous between two roles → don't guess
    return best_posting.id


async def link_outcomes_to_postings(
    session: AsyncSession,
    *,
    limit: int | None = None,
) -> LinkReport:
    """Populate ``job_posting_id`` on unlinked job-related outcomes, by ROLE.

    Only considers rows with ``job_posting_id IS NULL`` and a non-NULL
    ``target_company_id`` (company already resolved). Idempotent + deterministic.
    Commits once at the end (the candidate sets are small).
    """
    report = LinkReport()

    query = (
        select(OutcomeEvent)
        .where(OutcomeEvent.job_posting_id.is_(None))
        .where(OutcomeEvent.target_company_id.is_not(None))
        .where(OutcomeEvent.outcome_type.in_(_LINKABLE_TYPES))
        .order_by(OutcomeEvent.received_at.asc(), OutcomeEvent.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    events = (await session.execute(query)).scalars().all()
    report.scanned = len(events)

    # Cache candidate postings per company so N emails at one company hit the DB
    # once. OPEN postings only (closed roles aren't a live application target).
    candidates_by_company: dict[uuid.UUID, list[JobPosting]] = {}

    for event in events:
        company_id = event.target_company_id
        assert company_id is not None  # guarded by the WHERE above
        if company_id not in candidates_by_company:
            candidates_by_company[company_id] = list(
                (
                    await session.execute(
                        select(JobPosting)
                        .where(JobPosting.target_company_id == company_id)
                        .where(JobPosting.closed_at.is_(None))
                    )
                )
                .scalars()
                .all()
            )
        candidates = candidates_by_company[company_id]
        if not candidates:
            report.no_candidate += 1
            continue

        posting_id = _best_posting_id(event, candidates)
        if posting_id is None:
            report.ambiguous += 1
            continue
        event.job_posting_id = posting_id
        report.linked += 1

    await session.commit()
    logger.info(
        "outcome_posting_match.complete",
        extra={
            "scanned": report.scanned,
            "linked": report.linked,
            "no_candidate": report.no_candidate,
            "ambiguous": report.ambiguous,
        },
    )
    return report


__all__ = ["LinkReport", "link_outcomes_to_postings", "role_match_score"]
