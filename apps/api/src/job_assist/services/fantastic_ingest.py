"""Fantastic.jobs ingest orchestration — feat/fantastic-jobs-ingest.

Iterates ONLY the curated Workday/iCIMS employers (the ones whose boards block
Railway's egress IP) and ingests each via the Fantastic.jobs Apify actor,
reusing :class:`IngestionService` so the mapped roles flow through the exact
same path as the free adapters (content_hash dedupe → classifier → scorer →
hard rules → PostingSource, recorded in ``ingest_run``).

Greenhouse/Lever/Ashby are deliberately NOT here — they crawl fine on the free
adapters and must never be routed through the paid API.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.fantastic_jobs import DEFAULT_LIMIT, FantasticJobsAdapter
from job_assist.db.models.target_company import TargetCompany
from job_assist.services.ingestion import IngestionService

logger = structlog.get_logger(__name__)

# Only employers on these ATSes are sourced via Apify (their boards block the
# datacenter egress IP). Keep in lockstep with main.py._MANUAL_SOURCE_ATS.
FANTASTIC_SOURCED_ATS = ("workday", "icims")


async def list_fantastic_targets(session: AsyncSession) -> list[TargetCompany]:
    """Curated Workday/iCIMS employers with a handle (the Apify-sourced set)."""
    rows = await session.execute(
        select(TargetCompany)
        .where(TargetCompany.ats.in_(FANTASTIC_SOURCED_ATS))
        .where(TargetCompany.source == "curated")
        .where(TargetCompany.ats_handle.is_not(None))
        .order_by(TargetCompany.name)
    )
    return list(rows.scalars().all())


async def ingest_curated_via_fantastic(
    session: AsyncSession,
    token: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Run the Apify-sourced ingest for every curated Workday/iCIMS employer.

    One ``ingest_run`` per employer (``IngestionService.ingest_source`` commits
    per call and swallows per-employer failures into a ``failed`` run), so one
    bad board never aborts the batch. Returns per-employer counts for the cron
    log / the verify step.
    """
    targets = await list_fantastic_targets(session)
    service = IngestionService()
    results: list[dict[str, Any]] = []

    for tc in targets:
        ats_value = tc.ats.value if hasattr(tc.ats, "value") else str(tc.ats)
        adapter = FantasticJobsAdapter(
            organization=tc.name,
            domain=tc.domain,
            ats=ats_value,
            token=token,
            limit=limit,
        )
        async with adapter:
            run = await service.ingest_source(adapter, tc.ats_handle or tc.name, session)
        results.append(
            {
                "company": tc.name,
                "ats": ats_value,
                "handle": tc.ats_handle,
                "status": run.status,
                "postings_fetched": run.postings_fetched,
                "postings_new": run.postings_new,
                "postings_updated": run.postings_updated,
            }
        )

    logger.info(
        "fantastic_ingest.complete",
        employers=len(results),
        total_new=sum(r["postings_new"] for r in results),
    )
    return {"employers": len(results), "results": results}
