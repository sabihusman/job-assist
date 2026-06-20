"""Company enrichment — logo URL + one-sentence Gemini description.

PR #27 ships:
  * logo.dev URL construction (pure function, no IO)
  * Gemini Flash Lite description generation
  * Idempotent ``enrich_company`` with attempt-count gating
  * Sequential ``sweep_companies`` for the daily cron

Outputs land directly on ``target_company`` rows
(``description``, ``enriched_at``, ``enrichment_error``,
``enrichment_attempt_count``). Cached forever — the sweep skips any row
where ``description IS NOT NULL``.
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
from job_assist.db.models import TargetCompany
from job_assist.services.tracing import traceable

logger = logging.getLogger(__name__)

# logo.dev publishable token goes into a query string against the public
# /{domain} endpoint. Format current as of PR #27 — confirm via the linked
# docs in .env.example if it ever changes.
_LOGO_DEV_BASE = "https://img.logo.dev"

# Cap on enrichment_error column writes — keeps a long stack trace from
# bloating the row.
_ERROR_MAX_CHARS = 500

# Strict bounds for what a "one sentence" description can look like. The
# 250-char cutoff is the hard reject; the prompt asks for ≤180.
_DESC_HARD_MAX = 250

_PROMPT_TEMPLATE = (
    "Write one factual sentence (max 180 characters) describing what "
    "{company_name} does as a business. No marketing language. "
    "No superlatives. Just what they do."
)

EnrichmentStatus = Literal[
    "enriched",
    "skipped",
    "no_domain",
    "exhausted",
    "error",
    "not_found",
]


@dataclass(frozen=True)
class EnrichmentResult:
    """Outcome of a single ``enrich_company`` call."""

    status: EnrichmentStatus
    company_id: str | None = None
    error: str | None = None


@dataclass
class SweepSummary:
    """Counters returned by ``sweep_companies`` for the cron endpoint."""

    total: int = 0
    enriched: int = 0
    skipped: int = 0
    no_domain: int = 0
    errors: int = 0
    exhausted: int = 0
    error_details: list[dict[str, str]] = field(default_factory=list)

    def record(self, result: EnrichmentResult) -> None:
        self.total += 1
        if result.status == "enriched":
            self.enriched += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "no_domain":
            self.no_domain += 1
        elif result.status == "exhausted":
            self.exhausted += 1
        elif result.status == "error":
            self.errors += 1
            if result.company_id and result.error:
                self.error_details.append(
                    {"company_id": result.company_id, "error": result.error[:200]}
                )


# ── Pure helpers ─────────────────────────────────────────────────────────────


def build_logo_url(domain: str, token: str | None = None) -> str:
    """Return the logo.dev URL for *domain*.

    Pure function: no IO, no env reads. Pass the publishable token
    explicitly so this stays trivially testable; callers can pull it from
    ``settings.logo_dev_token`` at the call site.

    Raises ``ValueError`` if *domain* is empty / whitespace.
    """
    if not domain or not domain.strip():
        raise ValueError("domain is required to build a logo URL")
    cleaned = domain.strip()
    tok = token if token is not None else settings.logo_dev_token
    return f"{_LOGO_DEV_BASE}/{cleaned}?token={tok}"


def build_prompt(company_name: str) -> str:
    return _PROMPT_TEMPLATE.format(company_name=company_name)


def _validate_description(text: str) -> str:
    """Apply the post-LLM sanity rules — ``raise`` rather than truncate."""
    if "\n" in text or "\r" in text:
        raise ValueError("description contains newlines")
    if len(text) > _DESC_HARD_MAX:
        raise ValueError(f"description too long: {len(text)} chars (hard max {_DESC_HARD_MAX})")
    return text


# ── Gemini call ──────────────────────────────────────────────────────────────


@traceable(run_type="llm", name="gemini.company_description")
async def generate_description(
    company_name: str,
    domain: str | None,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Call Gemini Flash Lite for a one-sentence company description.

    Returns the trimmed text on success; raises ``ValueError`` if the model
    returns something that fails the hard length / newline checks.
    Defaults pull from settings; callers can override per-test.
    """
    # Lazy import keeps unit tests that monkey-patch this function from
    # needing google-genai installed.
    from google import genai
    from google.genai import types

    key = api_key if api_key is not None else settings.gemini_api_key
    if not key:
        raise RuntimeError("gemini_api_key is unset")

    client = genai.Client(api_key=key)
    used_model = model or settings.company_desc_model
    prompt = build_prompt(company_name)

    def _call() -> Any:
        return client.models.generate_content(
            model=used_model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0),
        )

    response = await asyncio.to_thread(_call)
    raw = getattr(response, "text", None) or ""
    text = raw.strip()
    if not text:
        raise ValueError("empty response from gemini")
    return _validate_description(text)


# ── Row-level orchestrator ───────────────────────────────────────────────────


async def _fetch_company(
    session: AsyncSession, target_company_id: uuid.UUID
) -> TargetCompany | None:
    return (
        await session.execute(select(TargetCompany).where(TargetCompany.id == target_company_id))
    ).scalar_one_or_none()


async def enrich_company(
    session: AsyncSession,
    target_company_id: uuid.UUID,
) -> EnrichmentResult:
    """Run the full enrich-one-row routine. Idempotent.

    Status meanings:
      * ``enriched``    — description set, error cleared, enriched_at stamped
      * ``skipped``     — description was already set; no LLM call
      * ``exhausted``   — attempt_count >= max; no LLM call until /retry
      * ``no_domain``   — domain is NULL; attempt counted, no LLM call
      * ``error``       — Gemini call (or post-validation) failed; counted
      * ``not_found``   — no row with that id; nothing to do
    """
    company = await _fetch_company(session, target_company_id)
    if company is None:
        return EnrichmentResult(status="not_found", company_id=str(target_company_id))

    cid = str(company.id)

    # Idempotency: cached-forever semantics.
    if company.description is not None:
        return EnrichmentResult(status="skipped", company_id=cid)

    # Attempt-count gate — operator must call /retry to reset.
    if company.enrichment_attempt_count >= settings.company_enrich_max_attempts:
        return EnrichmentResult(status="exhausted", company_id=cid)

    if not company.domain:
        company.enrichment_error = "missing domain"
        company.enrichment_attempt_count += 1
        await session.commit()
        return EnrichmentResult(status="no_domain", company_id=cid)

    try:
        description = await generate_description(company.name, company.domain)
    except Exception as exc:
        err = str(exc)[:_ERROR_MAX_CHARS]
        company.enrichment_error = err
        company.enrichment_attempt_count += 1
        await session.commit()
        logger.warning(
            "company_enrichment.error",
            extra={"company_id": cid, "error": err},
        )
        return EnrichmentResult(status="error", company_id=cid, error=err)

    company.description = description
    company.enriched_at = datetime.now(tz=UTC)
    company.enrichment_error = None
    # Leave attempt_count alone on success — operator can see how many
    # attempts it took before it landed.
    await session.commit()
    logger.info("company_enrichment.success", extra={"company_id": cid})
    return EnrichmentResult(status="enriched", company_id=cid)


# ── Sweep ────────────────────────────────────────────────────────────────────


@traceable(run_type="chain", name="company_enrichment_sweep")
async def sweep_companies(session: AsyncSession) -> SweepSummary:
    """Run ``enrich_company`` over every ``target_company`` row, sequentially.

    Sequential by design — the target list is small (≤30) and Gemini's
    free-tier RPM cap favours serial calls over a burst.
    """
    rows = (await session.execute(select(TargetCompany.id))).scalars().all()
    summary = SweepSummary()
    for company_id in rows:
        result = await enrich_company(session, company_id)
        summary.record(result)
    return summary


async def reset_attempts_and_retry(
    session: AsyncSession,
    target_company_id: uuid.UUID,
) -> EnrichmentResult:
    """Operator-only: zero the attempt counter, then re-run the enrichment."""
    company = await _fetch_company(session, target_company_id)
    if company is None:
        return EnrichmentResult(status="not_found", company_id=str(target_company_id))
    company.enrichment_attempt_count = 0
    company.enrichment_error = None
    # Also clear the description so a stale value doesn't short-circuit
    # the next call — operator is explicitly asking for a fresh attempt.
    company.description = None
    company.enriched_at = None
    await session.commit()
    return await enrich_company(session, target_company_id)
