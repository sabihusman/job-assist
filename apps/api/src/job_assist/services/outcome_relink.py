"""Re-link existing outcome_event rows to target_company (feat/outcome-company-linking).

Background: ``outcome_event.target_company_id`` is set at classification
time inside ``gmail/backfill.py``. The matcher there has two paths
(domain exact + fuzzy name) but in production fires only ~6% of the time
because (a) ``target_company.domain`` was uniformly NULL and (b) the
fuzzy matcher's strict ``len(candidates) == 1`` check and narrow suffix
regex missed common Gemini outputs like "the X Recruiting Team".

This module's ``relink_unmatched`` re-runs ``_match_target_company``
over the corpus of ALREADY-CLASSIFIED job-related outcomes whose
``target_company_id`` is NULL. Used by the
``POST /admin/outcomes/relink`` endpoint as a one-shot backfill after
either or both of:
  * the operator hand-fills ``target_company.domain`` (via the seed
    endpoint with ``backfill_nullables=true``), or
  * the matcher itself is softened in this PR (additional suffix
    patterns + leading-article strip + relaxed unique-candidate check).

Two re-match paths controlled by ``use_classifier``:
  * ``False`` (cheap, ~0 LLM cost): domain-only match using the
    ``outcome_event.from_domain`` we persisted at original ingest. Covers
    rows whose company-domain was unknown then and is known now.
  * ``True`` (slow, ~4s/row on Gemini free tier): re-derives
    ``extracted_company`` from the persisted ``raw_snippet`` via the
    classifier, then runs the full matcher. Covers rows the domain path
    misses (the majority — ATS no-reply senders use ``ashbyhq.com`` /
    ``greenhouse.io``, not the company's own domain).

Re-runs are safe: rows are filtered to ``target_company_id IS NULL``, so
existing links are never overwritten. The classifier's
``outcome_type`` output during re-derivation is intentionally
**discarded** — we only need ``extracted_company``. Bumping the original
classification is the job of ``/admin/reclassify/sweep``, not this
endpoint.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import OutcomeEvent
from job_assist.gmail.backfill import _match_target_company
from job_assist.gmail.models import ClassificationResult, RawEmail

logger = logging.getLogger(__name__)


# Outcome types we attempt to re-link. Mirrors the
# ``_is_job_related`` predicate in ``gmail/backfill.py`` — there's no
# point spending a Gemini call to re-derive ``extracted_company`` on a
# row the original classifier marked ``unrelated`` / ``unclassified``.
_JOB_RELATED_TYPES_TO_RELINK: tuple[str, ...] = (
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


class _ClassifierLike(Protocol):
    """Structural shape — eases unit-test mocking. Same as the one in
    gmail/backfill.py, repeated here to avoid an import cycle for tests
    that stub one without the other."""

    async def classify(self, email: RawEmail) -> ClassificationResult: ...


class RelinkReport(BaseModel):
    """Counters returned by ``relink_unmatched`` for the admin endpoint."""

    scanned: int = 0
    domain_matched: int = 0
    fuzzy_matched: int = 0
    classifier_errors: int = 0
    unmatched: int = 0


def _raw_email_from_outcome(event: OutcomeEvent) -> RawEmail:
    """Build a minimal :class:`RawEmail` from persisted ``outcome_event``
    columns so the classifier can re-derive ``extracted_company``.

    The original classifier was called on the full body_text. We only
    have ``raw_snippet`` (Gmail's pre-computed ~200-char preview) plus
    ``subject``. Re-derivation quality is therefore lower than the
    original — usable for fuzzy-name re-matching but not a substitute
    for a full re-classification. Body fields the prompt template
    expects but we don't have are filled with empty strings.
    """
    return RawEmail(
        message_id=event.email_message_id,
        from_address=event.from_address,
        from_domain=event.from_domain,
        subject=event.subject,
        received_at=event.received_at,
        body_text="",
        body_html="",
        snippet=event.raw_snippet or "",
    )


async def relink_unmatched(
    session: AsyncSession,
    classifier: _ClassifierLike | None = None,
    *,
    use_classifier: bool = False,
    limit: int | None = None,
) -> RelinkReport:
    """Re-run ``_match_target_company`` over unlinked job-related outcomes.

    Args:
      session: The async DB session.
      classifier: Required iff ``use_classifier=True``. The endpoint
        constructs one via the same ``_build_gmail_runtime()`` helper
        that ``/admin/gmail/poll`` uses, so the env-var guard is
        single-sourced.
      use_classifier: When True, rows that fail the domain path get
        their ``extracted_company`` re-derived via Gemini on the
        persisted ``raw_snippet``. Expensive: ~4s per call under the
        free-tier throttle. When False, only the domain path runs —
        cheap, no LLM cost.
      limit: Optional cap on rows scanned. Useful for paginating
        through a large unlinked corpus or smoke-testing the endpoint
        with ``?limit=5`` before committing to the full re-link.

    Returns a :class:`RelinkReport` with the per-bucket tallies.
    Commits periodically (every 25 updates) so a mid-run crash doesn't
    lose progress — matches the pattern in ``gmail/backfill.py``.
    """
    if use_classifier and classifier is None:
        raise ValueError("classifier is required when use_classifier=True")

    report = RelinkReport()

    query = (
        select(OutcomeEvent)
        .where(OutcomeEvent.target_company_id.is_(None))
        .where(OutcomeEvent.outcome_type.in_(_JOB_RELATED_TYPES_TO_RELINK))
        # Deterministic order so paginated invocations are stable.
        .order_by(OutcomeEvent.received_at.asc(), OutcomeEvent.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    rows = (await session.execute(query)).scalars().all()
    report.scanned = len(rows)
    logger.info("outcome_relink.scanned", extra={"count": report.scanned})

    updates_since_commit = 0
    for event in rows:
        # 1. Domain-only path first — no LLM cost.
        tc = await _match_target_company(
            session,
            from_domain=event.from_domain,
            extracted_company=None,
        )
        matched_via_domain = tc is not None

        # 2. Re-derive extracted_company via the classifier when domain
        #    failed and the operator opted in. ``outcome_type`` from the
        #    re-classification is discarded — we only want the name.
        if tc is None and use_classifier and classifier is not None:
            try:
                verdict = await classifier.classify(_raw_email_from_outcome(event))
            except Exception:
                logger.exception(
                    "outcome_relink.classify_failed",
                    extra={"outcome_event_id": str(event.id)},
                )
                report.classifier_errors += 1
                continue
            if verdict.extracted_company:
                tc = await _match_target_company(
                    session,
                    from_domain=event.from_domain,
                    extracted_company=verdict.extracted_company,
                )

        if tc is None:
            report.unmatched += 1
            continue

        event.target_company_id = tc.id
        if matched_via_domain:
            report.domain_matched += 1
        else:
            report.fuzzy_matched += 1
        updates_since_commit += 1
        if updates_since_commit >= 25:
            await session.commit()
            updates_since_commit = 0

    if updates_since_commit > 0:
        await session.commit()

    logger.info(
        "outcome_relink.complete",
        extra={
            "finished_at": datetime.now(UTC).isoformat(),
            "scanned": report.scanned,
            "domain_matched": report.domain_matched,
            "fuzzy_matched": report.fuzzy_matched,
            "classifier_errors": report.classifier_errors,
            "unmatched": report.unmatched,
        },
    )
    return report


__all__ = ["RelinkReport", "relink_unmatched"]
