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
        query: str,
        max_results_per_page: int = ...,
    ) -> list[str]: ...

    async def get_message(self, message_id: str) -> RawEmail: ...


class _ClassifierLike(Protocol):
    async def classify(self, email: RawEmail) -> ClassificationResult: ...


# ── Helpers ───────────────────────────────────────────────────────────────────


def _should_prefilter(email: RawEmail) -> bool:
    """True when the email's ``from_domain`` is in the cheap deny-list."""
    return email.from_domain in OBVIOUS_NON_JOB_DOMAINS


# Corporate / recruiting suffixes stripped before fuzzy matching. Adding
# ``team``/``recruiting``/``careers``/``talent`` to the original company-
# suffix list (PR feat/outcome-company-linking) so Gemini's
# ``extracted_company`` of e.g. ``"the MeridianLink Recruiting Team"``
# normalises to the same key as ``"MeridianLink"``. The 5.9% match rate
# observed in production was largely killed by these patterns falling
# through as noise.
_COMPANY_SUFFIX_RE = re.compile(
    r"[\s,]+(?:"
    r"inc|llc|ltd|corp|corporation|company|co|holdings?|group|plc|gmbh"
    r"|team|recruiting|recruitment|careers|career|talent|hr|people"
    r")\.?\s*$",
    re.IGNORECASE,
)

# Article prefixes Gemini sometimes prepends (``"the MeridianLink team"``).
# Stripped before suffix removal so the suffix-trim catches the tail.
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def _normalize_company(name: str) -> str:
    """Lowercase, strip articles + corporate/recruiting suffixes + punctuation.

    Run the suffix regex twice — once handles ``"Recruiting Team"`` style
    double-suffixes (e.g. ``"MeridianLink Recruiting Team"`` → ``"MeridianLink
    Recruiting"`` → ``"MeridianLink"``). Two passes is enough for every
    real pattern; the cost is two regex calls per row at match time,
    negligible against the ~30-row corpus.
    """
    de_articled = _LEADING_ARTICLE_RE.sub("", name).strip()
    no_suffix = _COMPANY_SUFFIX_RE.sub("", de_articled).strip()
    no_suffix = _COMPANY_SUFFIX_RE.sub("", no_suffix).strip()
    return re.sub(r"[^\w]+", "", no_suffix).lower()


async def _match_target_company(
    session: AsyncSession,
    *,
    from_domain: str,
    extracted_company: str | None,
) -> TargetCompany | None:
    """Resolve a TargetCompany via (1) domain exact match, then (2) fuzzy name match.

    Returns None when neither path finds a row.

    PR feat/outcome-company-linking: the unique-candidate check is
    relaxed to "take the first match" when normalised-name candidates
    tie. Since ``target_company.name`` is UNIQUE in the schema, ties are
    only possible when two distinct names normalise to the same key
    (e.g. ``"Acme"`` and ``"Acme Corp"``). Taking the first such row is
    better than dropping the match entirely — same company, different
    legal-name variant.
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
        if rows:
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
    if candidates:
        return candidates[0]
    return None


def _is_job_related(outcome_type: str) -> bool:
    return outcome_type not in ("unrelated", "unclassified")


# ── Query builders ────────────────────────────────────────────────────────────


def build_date_range_query(after: datetime, before: datetime | None = None) -> str:
    """Day-granular Gmail search query (used by backfill).

    Returns e.g. ``"after:2026/05/15 before:2026/05/17"``. Day granularity
    is enough for a multi-month historical sweep; the orchestrator's
    per-message idempotency check filters out anything already classified.
    """
    parts = [f"after:{after.strftime('%Y/%m/%d')}"]
    if before is not None:
        parts.append(f"before:{before.strftime('%Y/%m/%d')}")
    return " ".join(parts)


def build_after_query(after: datetime) -> str:
    """Second-granular Gmail search query (used by poll).

    Gmail's ``after:`` operator accepts a Unix timestamp directly. The
    poll uses this so the 15-minute cron doesn't drag in a full day's
    worth of messages on every run.
    """
    return f"after:{int(after.timestamp())}"


# ── Orchestrator core ────────────────────────────────────────────────────────


async def _run_email_ingest(
    session: AsyncSession,
    gmail: _GmailClientLike,
    classifier: _ClassifierLike,
    *,
    query: str,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> BackfillReport:
    """Shared orchestrator. Lists IDs via *query*, classifies, inserts.

    Both ``run_backfill`` and ``run_poll`` delegate here so the
    fetch / pre-filter / classify / link / insert / flush body is
    single-sourced.
    """
    report = BackfillReport()

    message_ids = await gmail.list_message_ids(query=query)
    report.message_ids_listed = len(message_ids)
    logger.info(
        "gmail.ingest.listed",
        extra={"query": query, "count": report.message_ids_listed},
    )

    if not message_ids:
        return report

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
            logger.exception("gmail.ingest.fetch_failed", extra={"message_id": msg_id})
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
            logger.exception("gmail.ingest.classify_failed", extra={"message_id": msg_id})
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


# ── Public wrappers ──────────────────────────────────────────────────────────


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
    query = build_date_range_query(window_start, window_end)

    report = await _run_email_ingest(
        session, gmail, classifier, query=query, on_progress=on_progress
    )
    # Stamp the window descriptors on the way out so the existing endpoint
    # response shape stays unchanged for the backfill caller.
    report.days_back = days_back
    report.window_start = window_start
    report.window_end = window_end
    return report


# Default lookback when ``outcome_event`` is empty (e.g. fresh deploy before
# the operator has run a backfill). Gmail's ``after:`` operator handles
# arbitrary lookback so this can grow without changing the protocol.
POLL_BOOTSTRAP_LOOKBACK = timedelta(hours=24)


async def run_poll(
    session: AsyncSession,
    gmail: _GmailClientLike,
    classifier: _ClassifierLike,
) -> BackfillReport:
    """Poll Gmail for new messages since the most recent classified outcome.

    Watermark = ``MAX(outcome_event.received_at)``, falling back to
    ``now() - POLL_BOOTSTRAP_LOOKBACK`` (24 h) when the table is empty.
    The watermark is *derived* from data every run — no separate state
    table to drift out of sync.
    """
    from sqlalchemy import func

    now = datetime.now(tz=UTC)
    watermark_row = await session.execute(select(func.max(OutcomeEvent.received_at)))
    watermark: datetime | None = watermark_row.scalar_one_or_none()
    watermark_in_future = False
    if watermark is None:
        watermark = now - POLL_BOOTSTRAP_LOOKBACK
    else:
        if watermark.tzinfo is None:
            # Defensive: timestamps in DB are TIMESTAMP WITH TIME ZONE, but if a
            # naive value ever lands here, treat it as UTC rather than crash.
            watermark = watermark.replace(tzinfo=UTC)
        # fix/gmail-watermark: defense-in-depth against a legacy future-dated
        # row. client.parse_message now clamps received_at to now() on the way
        # in, but a row inserted before that fix could still sit in the future
        # and freeze the poll (after:<future> matches nothing, forever). Clamp
        # the watermark and flag the anomaly so the sweep reads unhealthy.
        if watermark > now:
            watermark_in_future = True
            logger.warning(
                "gmail.poll.watermark_in_future: watermark=%s now=%s — clamped",
                watermark.isoformat(),
                now.isoformat(),
            )
            watermark = now

    query = build_after_query(watermark)
    report = await _run_email_ingest(session, gmail, classifier, query=query)
    report.watermark_used = watermark
    report.watermark_in_future = watermark_in_future

    # Re-query MAX(received_at) after the run so the operator can see how
    # far the watermark advanced (or didn't).
    new_watermark = (
        await session.execute(select(func.max(OutcomeEvent.received_at)))
    ).scalar_one_or_none()
    if new_watermark is not None and new_watermark.tzinfo is None:
        new_watermark = new_watermark.replace(tzinfo=UTC)
    report.watermark_advanced_to = new_watermark
    return report
