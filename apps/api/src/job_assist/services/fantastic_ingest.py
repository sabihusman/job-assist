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

import httpx
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


def apify_domain_for(tc: TargetCompany) -> str | None:
    """The domain the Apify path should target for this employer.

    Prefers ``adapter_config.apify_domain`` when set, else falls back to the
    company's ``domain``. This decouples the Apify-indexed domain from the
    company domain: e.g. John Hancock's jobs are indexed under the parent
    ``manulife.com`` in the actor's DB, but ``domain`` stays ``johnhancock.com``
    so Gmail outcome-matching is unaffected. Generalizes to any employer whose
    Apify-indexed domain differs from their email domain.
    """
    cfg = tc.adapter_config if isinstance(tc.adapter_config, dict) else {}
    override = cfg.get("apify_domain")
    return str(override) if override else tc.domain


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
            domain=apify_domain_for(tc),
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


async def probe_fantastic_domain(
    token: str, *, domain: str, limit: int = 5, title_filter: bool = False
) -> dict[str, Any]:
    """Diagnostic: a probe pull for one employer domain. Does NOT persist.

    ``title_filter=False`` (default) drops the PM/PO filter to tell "no PM/PO
    roles here" (domain returns jobs unfiltered, none match) from "domain
    targeting is off" (0 even unfiltered). ``title_filter=True`` keeps the
    filter (a known-valid query) — useful to fetch a real matching record for
    field inspection.

    On an Apify HTTP error the error is SURFACED (status + body) instead of
    bubbling to a generic 500. On success it returns the count, sample titles,
    the first record's ``field_keys`` (to see what fields exist — e.g. a
    department/taxonomy), and a trimmed ``sample_record``.
    """
    adapter = FantasticJobsAdapter(
        organization=domain,
        domain=domain,
        ats="workday",  # irrelevant for the probe (no persist, no IngestRun)
        token=token,
        limit=limit,
        title_filter=title_filter,
    )
    try:
        async with adapter:
            raws = await adapter.fetch_postings("probe")
    except httpx.HTTPStatusError as exc:
        body = ""
        try:
            body = exc.response.text[:600]
        except Exception:
            body = "<unreadable>"
        return {
            "domain": domain,
            "title_filter": title_filter,
            "error": True,
            "apify_status": exc.response.status_code,
            "apify_body": body,
        }

    titles = [
        str(r.raw_payload.get("title") or "") for r in raws if isinstance(r.raw_payload, dict)
    ]
    field_keys: list[str] = []
    sample_record: dict[str, Any] | None = None
    if raws and isinstance(raws[0].raw_payload, dict):
        rec = {k: v for k, v in raws[0].raw_payload.items() if k != "organization_url"}
        field_keys = sorted(rec.keys())
        if isinstance(rec.get("description_text"), str):
            rec["description_text"] = rec["description_text"][:120] + "…"
        sample_record = rec
    return {
        "domain": domain,
        "title_filter": title_filter,
        "count": len(raws),
        "sample_titles": titles[:limit],
        "field_keys": field_keys,
        "sample_record": sample_record,
    }
