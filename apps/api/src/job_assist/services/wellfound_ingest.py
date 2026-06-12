"""Wellfound ingest orchestration — feat/wellfound-ingest.

Runs ONE role query against the clearpath Apify actor, then discovers companies
FROM the returned postings (the query-driven inversion of the company-keyed
curated/fantastic path) and drives each company's group through the standard
``IngestionService.ingest_source`` pipeline. The whole dedupe → classify →
score → hard-rules → embed path is reused unchanged.

Company materialization: each discovered company becomes (or resolves to) a
``target_company`` shell with ``source='wellfound'``, ``tier=NULL``,
``ats='unknown'`` — Wellfound is the POSTING's source (recorded on
``posting_source.ats='wellfound'``), not the company's ATS. Cross-source: a
company that ALSO exists curated/applied/broad is resolved by NORMALIZED NAME
and reused (one ``target_company``, one ``JobPosting`` per role via content_hash,
two ``posting_source`` rows) — never duplicated.

Query-driven only: these shells are NEVER added to a recurring plan. The daily
plan (``source IN ('curated','broad')``) and the fantastic sweep
(``source IN ('curated','warm_path')``) both exclude ``'wellfound'``; this
service is the only thing that ever sweeps them.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.base import Adapter
from job_assist.adapters.wellfound import (
    PrefetchedCompanyAdapter,
    WellfoundFetchError,
    WellfoundQuery,
    company_name_of,
)
from job_assist.db.models.target_company import TargetCompany
from job_assist.services.company_name_match import normalize_company_name
from job_assist.services.ingestion import IngestionService

logger = structlog.get_logger(__name__)

# The default role query. Wellfound is queried by role URL slug; the operator's
# first-PM-hire / 0-to-1 targets concentrate under the product-manager role page.
DEFAULT_ROLE = "product-manager"


async def _ensure_wellfound_company(session: AsyncSession, *, name: str) -> TargetCompany | None:
    """Resolve a discovered Wellfound company to a ``target_company`` row.

    Keyed by NORMALIZED name (Wellfound companies have no ats_handle): an
    existing row with the same normalized name — curated/applied/broad/a prior
    wellfound shell — is REUSED untouched (this is what makes the cross-source
    dedupe land: the curated and Wellfound copies of a role share one company →
    one JobPosting via content_hash). Otherwise a fresh shell is inserted with
    ``source='wellfound'``, ``tier=NULL``, ``ats='unknown'``. Returns None when
    the name normalizes to nothing (a vendor/junk name) so the caller skips it.
    """
    norm = normalize_company_name(name)
    if not norm:
        return None

    # Scan + match in Python (the corpus is small; the normalizer is regex-based
    # so it can't run in SQL). Mirrors _ensure_shell_company's name-resolution.
    existing = [
        r
        for r in (await session.execute(select(TargetCompany))).scalars().all()
        if normalize_company_name(r.name) == norm
    ]
    if existing:
        return existing[0]

    shell = TargetCompany(
        name=name.strip(),
        source="wellfound",
        tier=None,
        ats="unknown",  # the company's real ATS is unknown; Wellfound is the
        # posting's source (posting_source.ats='wellfound'), not the company's.
    )
    session.add(shell)
    await session.flush()
    return shell


async def ingest_wellfound(
    session: AsyncSession,
    token: str,
    *,
    role: str = DEFAULT_ROLE,
    only_remote: bool = True,
    page_limit: int = 1,
    monitor_mode: bool = False,
) -> dict[str, Any]:
    """Query Wellfound for one role, discover companies, ingest per company.

    Returns the Gate-1 readout: fetched / kept / skipped_quality counts, the
    estimated run cost + cost-guard flag, per-company ingest results (new /
    updated), and the company count. One ``ingest_run`` per discovered company
    (``ingest_source`` commits per call and swallows per-company failures into a
    ``failed`` run) so one bad company never aborts the sweep. A whole-run actor
    failure soft-fails with ``ok=False`` rather than raising — the cron must
    never crash.
    """
    from datetime import UTC, datetime

    query = WellfoundQuery(
        token=token,
        role=role,
        only_remote=only_remote,
        page_limit=page_limit,
        monitor_mode=monitor_mode,
    )
    try:
        async with query:
            raws = await query.run()
    except WellfoundFetchError as exc:
        logger.warning("wellfound_ingest.fetch_failed", role=role, error=str(exc)[:300])
        return {
            "ok": False,
            "role": role,
            "error": str(exc)[:300],
            "fetched": 0,
            "kept": 0,
            "skipped_quality": 0,
            "companies": 0,
            "postings_new": 0,
            "postings_updated": 0,
            "estimated_cost_usd": 0.0,
            "cost_guard_tripped": False,
            "results": [],
        }

    # ── Group quality-passing records by discovered company ──────────────────
    by_company: dict[str, list[Any]] = {}
    skipped_no_company = 0
    for raw in raws:
        rec = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
        name = company_name_of(rec)
        if not name:
            skipped_no_company += 1
            continue
        by_company.setdefault(name, []).append(raw)

    service = IngestionService()
    results: list[dict[str, Any]] = []
    total_new = 0
    total_updated = 0

    for name, group in by_company.items():
        shell = await _ensure_wellfound_company(session, name=name)
        if shell is None:
            skipped_no_company += len(group)
            continue
        adapter = PrefetchedCompanyAdapter(group)
        async with adapter:
            # Role URL already scopes to PM — do NOT apply the title pre-filter
            # (it would drop founding-PM / "first product hire" titles the
            # operator ranks highest). target_company=shell links the postings
            # to the discovered company.
            run = await service.ingest_source(
                cast(Adapter, adapter),
                shell.name,
                session,
                target_company=shell,
            )
        if run.status != "failed":
            shell.last_swept_at = datetime.now(tz=UTC)
        total_new += run.postings_new
        total_updated += run.postings_updated
        results.append(
            {
                "company": shell.name,
                "status": run.status,
                "postings_fetched": run.postings_fetched,
                "postings_new": run.postings_new,
                "postings_updated": run.postings_updated,
            }
        )

    await session.commit()  # persist the last_swept_at stamps

    logger.info(
        "wellfound_ingest.complete",
        role=role,
        fetched=query.fetched,
        kept=query.kept,
        skipped_quality=query.skipped_quality,
        companies=len(results),
        new=total_new,
        estimated_cost_usd=query.estimated_cost_usd,
        cost_guard_tripped=query.cost_guard_tripped,
    )
    return {
        "ok": True,
        "role": role,
        "fetched": query.fetched,
        "kept": query.kept,
        "skipped_quality": query.skipped_quality,
        "skipped_no_company": skipped_no_company,
        "companies": len(results),
        "postings_new": total_new,
        "postings_updated": total_updated,
        "estimated_cost_usd": query.estimated_cost_usd,
        "cost_guard_tripped": query.cost_guard_tripped,
        "monitor_mode": monitor_mode,
        "results": results,
    }
