"""Gmail backfill orchestrator.

Pulls the last ``days_back`` days of mail, classifies each message with
Gemini Flash Lite, and writes one ``outcome_event`` per classified message.
Idempotent at the message level: re-running over the same window is safe
because each message ID is checked against ``outcome_event.email_message_id``
before any LLM call or insert.

Out of scope for this PR (deferred to a later week):
  * Linking ``outcome_event`` to a specific ``job_posting``. We have no
    reliable way to match an email to a posting in our DB yet.
  * Creating ``application_state`` rows for ``application_confirmation``
    outcomes — that table requires a ``job_posting_id`` NOT NULL, which
    we don't have until the application↔posting link lands.

The backfill writes the raw classification verdict + target_company link
where possible; the operator can pivot off ``outcome_event`` rows directly.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import OutcomeEvent, TargetCompany
from job_assist.gmail.classifier import CLASSIFIER_VERSION
from job_assist.gmail.models import BackfillReport, ClassificationResult, RawEmail

logger = logging.getLogger(__name__)


# Domains that virtually never carry job-search content. Kept short on
# purpose — when in doubt, let Gemini decide (better one wasted call than
# a missed real recruiter email). Notably we DO NOT include linkedin.com
# or any common email-sender domain; LinkedIn InMails are job-search
# signal we want to keep.
OBVIOUS_NON_JOB_DOMAINS: frozenset[str] = frozenset(
    {
        "github.com",
        "stripe.com",
        "openai.com",
    }
)


# ── Public types ──────────────────────────────────────────────────────────────


class _GmailClientLike(Protocol):
    """Structural shape we use from GmailClient — eases unit-test mocking."""

    async def list_message_ids(
        self,
        after: datetime,
        before: datetime | None = ...,
        max_results_per_page: int = ...,
    ) -> list[str]: ...

    async def get_message(self, message_id: str) -> RawEmail: ...


class _ClassifierLike(Protocol):
    async def classify(self, email: RawEmail) -> ClassificationResult: ...


# ── Helpers ───────────────────────────────────────────────────────────────────


def _should_prefilter(email: RawEmail) -> bool:
    """True when the email's ``from_domain`` is in the cheap deny-list."""
    return email.from_domain in OBVIOUS_NON_JOB_DOMAINS


_COMPANY_SUFFIX_RE = re.compile(
    r"[\s,]+(?:inc|llc|ltd|corp|corporation|company|co|holdings?|group|plc|gmbh)\.?\s*$",
    re.IGNORECASE,
)


def _normalize_company(name: str) -> str:
    """Lowercase, strip corporate suffixes and punctuation for fuzzy matching."""
    no_suffix = _COMPANY_SUFFIX_RE.sub("", name).strip()
    return re.sub(r"[^\w]+", "", no_suffix).lower()


async def _match_target_company(
    session: AsyncSession,
    *,
    from_domain: str,
    extracted_company: str | None,
) -> TargetCompany | None:
    """Resolve a TargetCompany via (1) domain exact match, then (2) fuzzy name match.

    Returns None when neither path finds a unique row.
    """
    # 1. Domain exact match — only when target_company.domain is populated.
    if from_domain:
        rows = (
            (
                await session.execute(
                    select(TargetCompany).where(TargetCompany.domain == from_domain)
                )
            )
            .scalars()
            .all()
        )
        if len(rows) == 1:
            return rows[0]

    if not extracted_company:
        return None

    # 2. Normalised-name match. Pull all rows and compare in Python rather
    #    than a SQL LOWER(...) IN (...) because the seed list is small (~30).
    target_norm = _normalize_company(extracted_company)
    if not target_norm:
        return None

    all_rows = (await session.execute(select(TargetCompany))).scalars().all()
    candidates = [r for r in all_rows if _normalize_company(r.name) == target_norm]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _is_job_related(outcome_type: str) -> bool:
    return outcome_type not in ("unrelated", "unclassified")


# ── Orchestrator ─────────────────────────────────────────────────────────────


async def run_backfill(
    session: AsyncSession,
    gmail: _GmailClientLike,
    classifier: _ClassifierLike,
    *,
    days_back: int = 60,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> BackfillReport:
    """Run a Gmail backfill ending at ``now()``, ``days_back`` wide."""
    window_end = datetime.now(tz=UTC)
    window_start = window_end - timedelta(days=days_back)

    report = BackfillReport(
        days_back=days_back,
        window_start=window_start,
        window_end=window_end,
    )

    message_ids = await gmail.list_message_ids(after=window_start, before=window_end)
    report.message_ids_listed = len(message_ids)
    logger.info(
        "gmail.backfill.listed",
        extra={"days_back": days_back, "count": report.message_ids_listed},
    )

    # ── Pre-load already-classified message IDs in this window ───────────────
    existing_rows = (
        (
            await session.execute(
                select(OutcomeEvent.email_message_id).where(
                    OutcomeEvent.email_message_id.in_(message_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    already_seen: set[str] = set(existing_rows)
    report.skipped_already_classified = len(already_seen)

    for idx, msg_id in enumerate(message_ids, start=1):
        if msg_id in already_seen:
            if on_progress is not None:
                await on_progress(idx, report.message_ids_listed)
            continue

        # ── Fetch + parse ────────────────────────────────────────────────────
        try:
            email = await gmail.get_message(msg_id)
        except Exception:
            logger.exception("gmail.backfill.fetch_failed", extra={"message_id": msg_id})
            report.fetch_errors += 1
            if on_progress is not None:
                await on_progress(idx, report.message_ids_listed)
            continue

        report.fetched += 1

        # ── Cheap pre-filter on sender domain ────────────────────────────────
        if _should_prefilter(email):
            report.skipped_prefilter += 1
            if on_progress is not None:
                await on_progress(idx, report.message_ids_listed)
            continue

        # ── Classify ─────────────────────────────────────────────────────────
        try:
            verdict = await classifier.classify(email)
        except Exception:
            logger.exception("gmail.backfill.classify_failed", extra={"message_id": msg_id})
            report.classifier_errors += 1
            if on_progress is not None:
                await on_progress(idx, report.message_ids_listed)
            continue

        if _is_job_related(verdict.outcome_type):
            report.classified_job_related += 1
        else:
            report.classified_unrelated += 1

        # ── Resolve target_company (only for job-related outcomes) ──────────
        target_company_id = None
        if _is_job_related(verdict.outcome_type):
            tc = await _match_target_company(
                session,
                from_domain=email.from_domain,
                extracted_company=verdict.extracted_company,
            )
            if tc is not None:
                target_company_id = tc.id
                report.target_company_links += 1

        # ── Insert outcome_event ────────────────────────────────────────────
        event = OutcomeEvent(
            email_message_id=email.message_id,
            email_thread_id=email.thread_id,
            from_address=email.from_address,
            from_domain=email.from_domain,
            subject=email.subject,
            received_at=email.received_at,
            outcome_type=verdict.outcome_type,
            classifier_version=CLASSIFIER_VERSION,
            classifier_confidence=verdict.confidence,
            raw_snippet=email.snippet or None,
            target_company_id=target_company_id,
        )
        session.add(event)
        report.outcome_events_inserted += 1

        # Flush periodically so a crash doesn't lose hours of work.
        if report.outcome_events_inserted % 25 == 0:
            await session.commit()

        if on_progress is not None:
            await on_progress(idx, report.message_ids_listed)

    await session.commit()
    return report
