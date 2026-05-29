"""JD summary enrichment — Gemini-generated operator-focused summaries.

PR #41 ships a six-status state machine over ``job_posting`` rows that
mirrors ``services/company_enrichment.py`` and
``services/division_enrichment.py``. Outputs land directly on the
``job_posting`` row (``jd_summary_markdown``, ``jd_summary_enriched_at``,
``jd_summary_enrichment_error``, ``jd_summary_enrichment_attempt_count``).

Cached forever — the sweep skips any row where
``jd_summary_markdown IS NOT NULL``.

Prompt design:
    Captures Scope / Org context / Hard requirements / Nice-to-haves /
    Comp / Location / Ambiguities. The ambiguity section is the
    load-bearing piece — the operator wants contradictions in the
    source JD surfaced explicitly, not smoothed over. Examples for the
    model are embedded in the system prompt itself.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.config import settings
from job_assist.db.models import JobPosting

logger = logging.getLogger(__name__)


_ERROR_MAX_CHARS = 500
# Cap stored summary length. The prompt asks for 100-200 words; this
# 4000-char bound is a defensive backstop for a runaway response, not
# a tight enforcement.
_SUMMARY_HARD_MAX = 4000
# Minimum JD text length before we'll even try summarizing. Below this
# the source is almost certainly junk (truncated, header-only, etc.)
# and Gemini just makes things up.
_JD_TEXT_MIN_CHARS = 100


_SYSTEM_PROMPT = """\
You are summarizing a job posting for an operator who needs to decide quickly \
whether the role is worth applying to. Output 100-200 words of markdown. Capture:

1. Role scope (what does this person do day-to-day?)
2. Team / org context if stated (who do they work with, what business unit)
3. Required qualifications (what's a hard requirement vs. nice-to-have)
4. Compensation and location if stated
5. AMBIGUITIES: if any of the above is unclear or contradictory in the source \
JD, flag it explicitly. Do NOT smooth over ambiguity. Examples:
   - If the title says "Senior PM" but the responsibilities span product \
management AND program management, say so.
   - If the JD lists "5+ years required" in one section and "3+ years" in \
another, flag the contradiction.
   - If the team / business unit is not specified, say "Team or business unit \
not specified in JD."

Output structure:

**Scope**: <1-2 sentence summary of what the role does>
**Org context**: <team/department/business unit, or "Not specified">
**Hard requirements**: <bullet list, 2-4 items max>
**Nice-to-haves**: <bullet list, optional>
**Comp**: <salary range or "Not stated">
**Location**: <remote/hybrid/onsite + cities if stated>
**Ambiguities**: <bullet list — items that the JD leaves unclear>

If any section has no content, write "Not specified" rather than omitting the \
section. Preserve ambiguity faithfully.
"""


EnrichmentStatus = Literal[
    "enriched",
    "skipped",
    "exhausted",
    "missing_context",
    "error",
    "not_found",
]


@dataclass(frozen=True)
class EnrichmentResult:
    """Outcome of a single ``enrich_one_posting`` call."""

    status: EnrichmentStatus
    posting_id: str | None = None
    error: str | None = None


@dataclass
class SweepSummary:
    """Counters returned by ``sweep_jd_summaries`` for the cron endpoint."""

    total: int = 0
    enriched: int = 0
    skipped: int = 0
    exhausted: int = 0
    missing_context: int = 0
    errors: int = 0
    error_details: list[dict[str, str]] = field(default_factory=list)

    def record(self, result: EnrichmentResult) -> None:
        self.total += 1
        if result.status == "enriched":
            self.enriched += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "exhausted":
            self.exhausted += 1
        elif result.status == "missing_context":
            self.missing_context += 1
        elif result.status == "error":
            self.errors += 1
            if result.posting_id and result.error:
                self.error_details.append(
                    {"posting_id": result.posting_id, "error": result.error[:200]}
                )


# ── Pure helpers ─────────────────────────────────────────────────────────────


def build_prompt(jd_text: str) -> str:
    """Render the user-message portion of the Gemini call.

    The system prompt (``_SYSTEM_PROMPT``) is injected separately by the
    SDK call below; this helper just shapes the raw JD text. Kept as a
    pure function so the prompt-template tests can assert structure.
    """
    cleaned = (jd_text or "").strip()
    return f"Job description follows:\n\n{cleaned}"


def get_system_prompt() -> str:
    """Return the system prompt verbatim.

    Exposed so the prompt-template test can assert the ambiguity clause
    is present without grepping the module source.
    """
    return _SYSTEM_PROMPT


def _validate_summary(text: str) -> str:
    """Hard-reject anything that fails the basic sanity checks.

    Returns the trimmed text on success; raises ``ValueError`` if the
    response looks unusable. We do NOT validate against the structural
    template (``**Scope**:`` etc.) — Gemini is generally faithful and a
    strict check would force /retry on harmless formatting drift.

    Control-char stripping: Postgres ``TEXT`` rejects NUL bytes
    (``\\x00``), and Gemini occasionally embeds them in outputs.
    Without this strip the eventual UPDATE blows up with
    ``invalid byte sequence``, which surfaced as 500s in the first
    production sweep (PR #44 fix). Other C0 controls except ``\\n``
    and ``\\t`` are also stripped — they have no business in a
    markdown summary and have caused issues with downstream renderers.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty summary")
    # Strip NUL + other C0 controls except newline / tab. We translate
    # them out rather than rejecting the response so a single bad byte
    # doesn't waste a Gemini call.
    cleaned = cleaned.translate(_C0_CONTROL_STRIP)
    if not cleaned.strip():
        raise ValueError("summary became empty after control-char strip")
    if len(cleaned) > _SUMMARY_HARD_MAX:
        raise ValueError(f"summary too long: {len(cleaned)} chars (hard max {_SUMMARY_HARD_MAX})")
    return cleaned


# Translation table that maps every C0 control character (\x00..\x1f)
# except newline (\x0a) and tab (\x09) to None (= remove). DEL (\x7f)
# is also stripped. Built once at module load.
_C0_CONTROL_STRIP = {cp: None for cp in [*range(0x00, 0x20), 0x7F] if cp not in (0x09, 0x0A)}


# ── Gemini call ──────────────────────────────────────────────────────────────


async def generate_summary(
    jd_text: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """Call Gemini Flash Lite for a structured JD summary.

    Returns the trimmed markdown on success; raises ``ValueError`` /
    ``RuntimeError`` on a problem. The function is monkey-patched in
    tests so the SDK never actually runs there.
    """
    from google import genai
    from google.genai import types

    key = api_key if api_key is not None else settings.gemini_api_key
    if not key:
        raise RuntimeError("gemini_api_key is unset")

    client = genai.Client(api_key=key)
    used_model = model or settings.jd_summary_model
    used_tokens = (
        max_output_tokens
        if max_output_tokens is not None
        else settings.jd_summary_max_output_tokens
    )
    user_message = build_prompt(jd_text)

    def _call() -> Any:
        return client.models.generate_content(
            model=used_model,
            contents=user_message,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=used_tokens,
                system_instruction=_SYSTEM_PROMPT,
            ),
        )

    response = await asyncio.to_thread(_call)
    raw = getattr(response, "text", None) or ""
    return _validate_summary(raw)


# ── Row-level orchestrator ───────────────────────────────────────────────────


async def _fetch_posting(session: AsyncSession, posting_id: uuid.UUID) -> JobPosting | None:
    return (
        await session.execute(select(JobPosting).where(JobPosting.id == posting_id))
    ).scalar_one_or_none()


async def enrich_one_posting(
    session: AsyncSession,
    posting_id: uuid.UUID,
) -> EnrichmentResult:
    """Run the full enrich-one-row routine. Idempotent.

    Status meanings:
      * ``enriched``        — summary set, error cleared, enriched_at stamped
      * ``skipped``         — summary was already set; no LLM call
      * ``exhausted``       — attempt_count >= max; no LLM call until /retry
      * ``missing_context`` — jd_text is empty / too short to summarise
      * ``error``           — Gemini call (or post-validation) failed
      * ``not_found``       — no row with that id
    """
    posting = await _fetch_posting(session, posting_id)
    if posting is None:
        return EnrichmentResult(status="not_found", posting_id=str(posting_id))

    pid = str(posting.id)

    if posting.jd_summary_markdown is not None:
        return EnrichmentResult(status="skipped", posting_id=pid)

    if posting.jd_summary_enrichment_attempt_count >= settings.jd_summary_enrich_max_attempts:
        return EnrichmentResult(status="exhausted", posting_id=pid)

    jd = (posting.jd_text or "").strip()
    if len(jd) < _JD_TEXT_MIN_CHARS:
        # Mark the attempt so a perpetually-empty row eventually moves
        # to ``exhausted`` rather than getting picked up every sweep.
        posting.jd_summary_enrichment_error = "missing or too-short jd_text"
        posting.jd_summary_enrichment_attempt_count += 1
        await session.commit()
        return EnrichmentResult(status="missing_context", posting_id=pid)

    try:
        summary = await generate_summary(jd)
    except Exception as exc:
        err = str(exc)[:_ERROR_MAX_CHARS]
        posting.jd_summary_enrichment_error = err
        posting.jd_summary_enrichment_attempt_count += 1
        await session.commit()
        logger.warning(
            "jd_summary_enrichment.error",
            extra={"posting_id": pid, "error": err},
        )
        return EnrichmentResult(status="error", posting_id=pid, error=err)

    posting.jd_summary_markdown = summary
    posting.jd_summary_enriched_at = datetime.now(tz=UTC)
    posting.jd_summary_enrichment_error = None
    # Leave attempt_count alone on success (audit trail).
    await session.commit()
    logger.info("jd_summary_enrichment.success", extra={"posting_id": pid})
    return EnrichmentResult(status="enriched", posting_id=pid)


# ── Sweep ────────────────────────────────────────────────────────────────────


async def sweep_jd_summaries(
    session: AsyncSession,
    limit: int = 100,
) -> SweepSummary:
    """Iterate eligible ``job_posting`` rows; call ``enrich_one_posting`` on each.

    Eligibility = (summary IS NULL AND attempt_count < max). Already-enriched
    and exhausted rows are filtered out at the SELECT level rather than at
    the per-row level so a single sweep doesn't waste cycles fetching
    everything just to skip 90% of it.

    ``limit`` caps the sweep to keep the daily cron under Gemini's free-tier
    RPM budget (15 calls/min * ~5 min comfortable window = ~75 rows max;
    default 100 leaves a small buffer for partial-credit retries).
    """
    eligible = (
        (
            await session.execute(
                select(JobPosting.id)
                .where(JobPosting.jd_summary_markdown.is_(None))
                # Skip stale/closed postings (Bestiary 5.18) — no point
                # spending a Gemini call summarizing a removed posting.
                .where(JobPosting.closed_at.is_(None))
                .where(
                    JobPosting.jd_summary_enrichment_attempt_count
                    < settings.jd_summary_enrich_max_attempts
                )
                # Bestiary 5.20: never-tried first (attempt_count ASC), oldest
                # within each tier (first_seen_at ASC). Drains the backlog
                # instead of letting newest-first (the old first_seen_at DESC)
                # perpetually starve the old tail, and avoids re-hammering rows
                # that already burned attempts on a transient failure.
                .order_by(
                    JobPosting.jd_summary_enrichment_attempt_count.asc(),
                    JobPosting.first_seen_at.asc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    summary = SweepSummary()
    for posting_id in eligible:
        # Wrap each row so an unexpected DB-level exception (e.g. an
        # invalid byte sequence from Gemini, a transient deadlock) can't
        # kill the whole sweep. ``enrich_one_posting`` already catches
        # Gemini exceptions itself; this is defence-in-depth for the
        # commit paths that live outside that try.
        try:
            result = await enrich_one_posting(session, posting_id)
        except Exception as exc:
            # The session is now in a failed-transaction state — roll
            # back so the next iteration starts clean.
            await session.rollback()
            err = str(exc)[:_ERROR_MAX_CHARS]
            logger.warning(
                "jd_summary_enrichment.sweep_row_failed",
                extra={"posting_id": str(posting_id), "error": err},
            )
            result = EnrichmentResult(
                status="error",
                posting_id=str(posting_id),
                error=err,
            )
        summary.record(result)
    return summary


async def reset_attempts_and_retry(
    session: AsyncSession,
    posting_id: uuid.UUID,
) -> EnrichmentResult:
    """Operator-only: zero the attempt counter, clear the summary, retry."""
    posting = await _fetch_posting(session, posting_id)
    if posting is None:
        return EnrichmentResult(status="not_found", posting_id=str(posting_id))
    posting.jd_summary_enrichment_attempt_count = 0
    posting.jd_summary_enrichment_error = None
    posting.jd_summary_markdown = None
    posting.jd_summary_enriched_at = None
    await session.commit()
    return await enrich_one_posting(session, posting_id)
