"""Division enrichment — discovery from job_posting + Gemini description.

Mirrors ``services/company_enrichment.py`` exactly (six-status state
machine, same validator pattern, same Gemini client wiring). Distinct
because:
  * Discovery: pull DISTINCT (company, dept, team) from ``job_posting``
    rather than starting from a static list of rows.
  * Prompt: grounded in the parent company's description.

The unique constraint on (target_company_id, department, team) with
``NULLS NOT DISTINCT`` keeps the discovery sweep idempotent — a second
run hits ON CONFLICT DO NOTHING for every existing tuple.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import distinct, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.config import settings
from job_assist.db.models import Division, JobPosting, TargetCompany
from job_assist.services.tracing import traceable

logger = logging.getLogger(__name__)


_ERROR_MAX_CHARS = 500
_DESC_HARD_MAX = 250

_PROMPT_TEMPLATE = (
    "{company_name} is described as: {company_description}\n"
    "\n"
    "Write one factual sentence (max 180 characters) describing what "
    "the {clause} at {company_name} typically does. Use only publicly "
    "available information. If the specific team is unknown, describe "
    "the typical responsibilities of such a team at a similar company. "
    "No marketing language. No superlatives."
)
_FALLBACK_COMPANY_DESC = "(no description available)"


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
    """Outcome of a single ``enrich_division`` call."""

    status: EnrichmentStatus
    division_id: str | None = None
    error: str | None = None


@dataclass
class DiscoverySummary:
    """Counters returned by ``discover_divisions``."""

    discovered: int = 0
    already_existed: int = 0


@dataclass
class SweepSummary:
    """Combined discovery + per-status enrichment counters."""

    # Discovery half:
    discovered: int = 0
    already_existed: int = 0
    # Enrichment half:
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
            if result.division_id and result.error:
                self.error_details.append(
                    {"division_id": result.division_id, "error": result.error[:200]}
                )


# ── Pure helpers ─────────────────────────────────────────────────────────────


def _clause(department: str | None, team: str | None) -> str:
    """Render the ``{clause}`` slot of the prompt template."""
    if department and team:
        return f"{department}/{team} division"
    if department:
        return f"{department} division"
    if team:
        return f"{team} team"
    # Discovery never inserts a row with both NULL, but defend anyway.
    return "team"


def build_prompt(
    company_name: str,
    company_description: str | None,
    department: str | None,
    team: str | None,
) -> str:
    """Render the Gemini prompt for one division.

    ``company_description`` may be ``None`` or empty (e.g. the company
    enrichment hasn't run yet). In that case the prompt substitutes a
    short placeholder and still asks for a typical-org-style answer —
    we don't refuse to enrich because the parent is missing context.
    """
    desc = (company_description or "").strip() or _FALLBACK_COMPANY_DESC
    return _PROMPT_TEMPLATE.format(
        company_name=company_name,
        company_description=desc,
        clause=_clause(department, team),
    )


def _validate_description(text: str) -> str:
    """Hard-reject anything that fails the ``≤250 chars`` or no-newlines rules."""
    cleaned = text.strip()
    if "\n" in cleaned or "\r" in cleaned:
        raise ValueError("description contains newlines")
    if len(cleaned) > _DESC_HARD_MAX:
        raise ValueError(f"description too long: {len(cleaned)} chars (hard max {_DESC_HARD_MAX})")
    return cleaned


# ── Gemini call ──────────────────────────────────────────────────────────────


@traceable(run_type="llm", name="gemini.division_description")
async def generate_description(
    company_name: str,
    company_description: str | None,
    department: str | None,
    team: str | None,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Call Gemini Flash Lite for a one-sentence division description."""
    from google import genai
    from google.genai import types

    key = api_key if api_key is not None else settings.gemini_api_key
    if not key:
        raise RuntimeError("gemini_api_key is unset")

    client = genai.Client(api_key=key)
    used_model = model or settings.division_desc_model
    prompt = build_prompt(company_name, company_description, department, team)

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


# ── Discovery ────────────────────────────────────────────────────────────────


async def discover_divisions(session: AsyncSession) -> DiscoverySummary:
    """Populate ``division`` from DISTINCT (company, dept, team) job_posting tuples.

    Skips tuples where BOTH department and team are NULL — those aren't
    organisational units worth tracking. Uses Postgres ``ON CONFLICT DO
    NOTHING`` against the ``uq_division_company_dept_team`` unique
    constraint (which is ``NULLS NOT DISTINCT``) so a re-run is cheap.
    """
    tuples = (
        await session.execute(
            select(
                distinct(JobPosting.target_company_id).label("target_company_id"),
                JobPosting.department,
                JobPosting.team,
            )
            .where(JobPosting.target_company_id.is_not(None))
            .where((JobPosting.department.is_not(None)) | (JobPosting.team.is_not(None)))
            # Skip stale/closed postings (Bestiary 5.18).
            .where(JobPosting.closed_at.is_(None))
        )
    ).all()

    summary = DiscoverySummary()
    if not tuples:
        return summary

    # Use INSERT ... ON CONFLICT DO NOTHING and rely on RETURNING to count
    # the rows we actually inserted versus the ones the constraint deflected.
    rows_to_insert = [
        {
            "target_company_id": tc_id,
            "department": dept,
            "team": team,
        }
        for tc_id, dept, team in tuples
    ]

    stmt = (
        pg_insert(Division)
        .values(rows_to_insert)
        .on_conflict_do_nothing(constraint="uq_division_company_dept_team")
        .returning(Division.id)
    )
    result = await session.execute(stmt)
    inserted_ids = result.scalars().all()
    await session.commit()

    summary.discovered = len(inserted_ids)
    summary.already_existed = len(tuples) - summary.discovered
    return summary


# ── Row-level orchestrator ───────────────────────────────────────────────────


async def _fetch_division(session: AsyncSession, division_id: uuid.UUID) -> Division | None:
    return (
        await session.execute(select(Division).where(Division.id == division_id))
    ).scalar_one_or_none()


async def _fetch_parent_company(
    session: AsyncSession, target_company_id: uuid.UUID
) -> TargetCompany | None:
    return (
        await session.execute(select(TargetCompany).where(TargetCompany.id == target_company_id))
    ).scalar_one_or_none()


async def enrich_division(session: AsyncSession, division_id: uuid.UUID) -> EnrichmentResult:
    """Six-status state machine mirroring ``enrich_company`` exactly.

    Status meanings:
      * ``enriched``        — description set, error cleared, enriched_at stamped
      * ``skipped``         — description was already set; no LLM call
      * ``exhausted``       — attempt_count >= max; no LLM call until /retry
      * ``missing_context`` — parent target_company missing or has empty name
      * ``error``           — Gemini call (or post-validation) failed
      * ``not_found``       — no row with that id; nothing to do
    """
    division = await _fetch_division(session, division_id)
    if division is None:
        return EnrichmentResult(status="not_found", division_id=str(division_id))

    did = str(division.id)

    if division.description is not None:
        return EnrichmentResult(status="skipped", division_id=did)

    if division.enrichment_attempt_count >= settings.division_enrich_max_attempts:
        return EnrichmentResult(status="exhausted", division_id=did)

    company = await _fetch_parent_company(session, division.target_company_id)
    if company is None or not (company.name or "").strip():
        division.enrichment_error = "missing parent company name"
        division.enrichment_attempt_count += 1
        await session.commit()
        return EnrichmentResult(status="missing_context", division_id=did)

    try:
        description = await generate_description(
            company.name,
            company.description,
            division.department,
            division.team,
        )
    except Exception as exc:
        err = str(exc)[:_ERROR_MAX_CHARS]
        division.enrichment_error = err
        division.enrichment_attempt_count += 1
        await session.commit()
        logger.warning(
            "division_enrichment.error",
            extra={"division_id": did, "error": err},
        )
        return EnrichmentResult(status="error", division_id=did, error=err)

    division.description = description
    division.enriched_at = datetime.now(tz=UTC)
    division.enrichment_error = None
    # attempt_count stays as-is on success (audit trail).
    await session.commit()
    logger.info("division_enrichment.success", extra={"division_id": did})
    return EnrichmentResult(status="enriched", division_id=did)


# ── Sweep ────────────────────────────────────────────────────────────────────


@traceable(run_type="chain", name="division_enrichment_sweep")
async def sweep_divisions(session: AsyncSession) -> SweepSummary:
    """Discover first, then enrich every division sequentially."""
    discovery = await discover_divisions(session)

    summary = SweepSummary(
        discovered=discovery.discovered,
        already_existed=discovery.already_existed,
    )

    rows = (await session.execute(select(Division.id))).scalars().all()
    for division_id in rows:
        result = await enrich_division(session, division_id)
        summary.record(result)
    return summary


async def reset_attempts_and_retry(
    session: AsyncSession, division_id: uuid.UUID
) -> EnrichmentResult:
    """Operator-only: zero the attempt counter, then re-run the enrichment."""
    division = await _fetch_division(session, division_id)
    if division is None:
        return EnrichmentResult(status="not_found", division_id=str(division_id))
    division.enrichment_attempt_count = 0
    division.enrichment_error = None
    # Clear the cached description too — operator is explicitly asking for a
    # fresh attempt, same semantics as the company-side reset_and_retry.
    division.description = None
    division.enriched_at = None
    await session.commit()
    return await enrich_division(session, division_id)
