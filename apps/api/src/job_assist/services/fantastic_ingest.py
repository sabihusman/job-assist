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

from typing import Any, cast

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.base import Adapter
from job_assist.adapters.fantastic_jobs import DEFAULT_LIMIT, FantasticJobsAdapter
from job_assist.db.models.target_company import TargetCompany
from job_assist.services.ingestion import IngestionService

logger = structlog.get_logger(__name__)

# Only employers on these ATSes are sourced via Apify (their boards block the
# datacenter egress IP). Keep in lockstep with main.py._MANUAL_SOURCE_ATS.
FANTASTIC_SOURCED_ATS = ("workday", "icims")


async def list_fantastic_targets(session: AsyncSession) -> list[TargetCompany]:
    """Curated Workday/iCIMS employers the Apify path can source.

    Requires a DOMAIN (Apify targets by ``domainFilter``), NOT an ats_handle —
    so Capital One / John Hancock (NULL handle, never given a Workday tenant)
    are included; the free Workday adapter can't crawl them, but Apify can.
    """
    rows = await session.execute(
        select(TargetCompany)
        .where(TargetCompany.ats.in_(FANTASTIC_SOURCED_ATS))
        .where(TargetCompany.source == "curated")
        .where(TargetCompany.domain.is_not(None))
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
            # FantasticJobsAdapter satisfies Adapter structurally; the only
            # mismatch is ``ats`` is per-INSTANCE here (one class serves both
            # workday + icims employers) vs the protocol's ClassVar. Runtime is
            # identical — cast to quiet the variance check.
            # Pass target_company=tc so the company link survives a NULL
            # ats_handle (Capital One / John Hancock) — resolving by handle
            # would drop the tier/company link for those.
            run = await service.ingest_source(
                cast(Adapter, adapter),
                tc.ats_handle or tc.domain or tc.name,
                session,
                target_company=tc,
            )
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


async def probe_fantastic_domain(token: str, *, domain: str, limit: int = 5) -> dict[str, Any]:
    """Diagnostic: an UNFILTERED Apify pull (no PM/PO title filter) for one
    employer domain. Does NOT persist — returns the count + sample titles.

    Tells "no PM/PO roles at this employer" (domain returns jobs unfiltered,
    but none match the PM/PO filter) from "domain targeting is off" (domain
    returns 0 even unfiltered) when the filtered ingest returns 0.
    """
    adapter = FantasticJobsAdapter(
        organization=domain,
        domain=domain,
        ats="workday",  # irrelevant for the probe (no persist, no IngestRun)
        token=token,
        limit=limit,
        title_filter=False,
    )
    async with adapter:
        raws = await adapter.fetch_postings("probe")
    titles = [
        str(r.raw_payload.get("title") or "") for r in raws if isinstance(r.raw_payload, dict)
    ]
    return {"domain": domain, "count": len(raws), "sample_titles": titles[:limit]}
