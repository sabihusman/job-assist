"""Broad-ingest runner (Slice 2 of broad-ingestion expansion).

Loops ``active`` rows in ``discovered_handle``, ingests each with the
title pre-filter ON (``apply_title_prefilter=True`` — PR #96), and
maintains per-handle lifecycle state so stale boards deregister.

Per handle:
  1. Ensure a thin ``target_company`` shell exists (name, ats,
     ats_handle, tier=NULL, domain=NULL) so ``ingest_source``'s
     canonical-name resolution + the postings-query OUTER JOIN both
     work unchanged. The shell is created idempotently — re-running
     never duplicates it, and an existing curated row is never
     overwritten.
  2. Build the right adapter (greenhouse / lever / ashby only — the
     trial ATSes; workday / icims need per-tenant adapter_config we
     don't have for discovered handles, so they're skipped with a
     logged note).
  3. ``ingest_source(adapter, handle, session, apply_title_prefilter=True)``.
  4. Write back ``last_ingested_at``; bump ``consecutive_empty_count``
     when the run surfaced zero rows, else reset it to 0.

Bounded by ``limit`` (default 50) so the trial can't accidentally
sweep more than intended. No weekly qualified-row cap yet — that's
Slice 3.

The runner is deliberately sequential (one handle at a time) to match
the existing daily-ingest cron's politeness profile and to keep the
Gemini-free ingest path predictable. Slice 3 can parallelize if the
full handle volume needs it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.base import Adapter, HandleNotFoundError
from job_assist.db.models import DiscoveredHandle, TargetCompany
from job_assist.services.ingestion import IngestionService

logger = logging.getLogger(__name__)

# Only these ATSes are swept in the broad trial. Workday + iCIMS need
# per-tenant adapter_config (wd_number/site, or careers_url) that a
# bare discovered handle doesn't carry, so they're out of scope here.
_BROAD_SUPPORTED_ATS: frozenset[str] = frozenset({"greenhouse", "lever", "ashby"})

# After this many consecutive empty/not-found pulls, a handle is
# auto-deactivated so the runner stops wasting API calls on a dead
# board. Conservative for the trial — a board legitimately between
# postings shouldn't drop on a single empty pull.
_DEACTIVATE_AFTER_EMPTY = 3


class BroadIngestHandleResult(BaseModel):
    """Per-handle outcome for the runner's response."""

    ats: str
    handle: str
    status: str  # IngestRun.status, or 'skipped_unsupported_ats'
    postings_fetched: int = 0
    postings_kept: int = 0  # new + updated = survived the title filter
    deactivated: bool = False


class BroadIngestReport(BaseModel):
    """Aggregate result of one broad-ingest run."""

    handles_considered: int = 0
    handles_ingested: int = 0
    handles_skipped_unsupported: int = 0
    shells_created: int = 0
    total_postings_fetched: int = 0
    total_postings_kept: int = 0
    handles_deactivated: int = 0
    per_handle: list[BroadIngestHandleResult] = []


async def seed_discovered_handles(
    session: AsyncSession,
    handles: list[tuple[str, str]],
    *,
    source: str = "hand_seed_trial",
) -> tuple[int, int]:
    """Upsert ``(ats, handle)`` pairs into ``discovered_handle``.

    Idempotent: a pair that already exists is skipped (never
    duplicated, never reset — its lifecycle counters survive a
    re-seed). Returns ``(inserted, skipped)``. Commits before
    returning. Shared by ``scripts/discover_handles.py`` (local seed)
    and ``POST /admin/discovered-handles/seed`` (production seed).
    """
    inserted = 0
    skipped = 0
    for ats, handle in handles:
        existing = (
            await session.execute(
                select(DiscoveredHandle)
                .where(DiscoveredHandle.ats == ats)
                .where(DiscoveredHandle.handle == handle)
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        session.add(DiscoveredHandle(ats=ats, handle=handle, source=source))
        inserted += 1
    await session.commit()
    return inserted, skipped


def _build_adapter(ats: str) -> Adapter:
    """Construct the adapter for a broad-supported ATS. Caller has
    already validated ``ats in _BROAD_SUPPORTED_ATS``."""
    from job_assist.adapters.ashby import AshbyAdapter
    from job_assist.adapters.greenhouse import GreenhouseAdapter
    from job_assist.adapters.lever import LeverAdapter

    if ats == "greenhouse":
        return GreenhouseAdapter()
    if ats == "lever":
        return LeverAdapter()
    return AshbyAdapter()


async def _ensure_shell_company(session: AsyncSession, *, ats: str, handle: str) -> bool:
    """Create a thin target_company shell for a discovered handle if one
    doesn't already exist. Returns True when a row was inserted.

    Idempotent + non-destructive: if a row with this ``ats_handle``
    already exists (curated OR a prior broad run), it is left untouched
    — we never overwrite a curated company's name/tier/domain. ``tier``
    is left NULL: broad-ingest companies have no pedigree tier;
    ``score_tier(NULL)`` already returns the neutral 50, and the
    postings query's OUTER JOIN tolerates the NULL.
    """
    existing = (
        await session.execute(select(TargetCompany).where(TargetCompany.ats_handle == handle))
    ).scalar_one_or_none()
    if existing is not None:
        return False
    # Name is a best-effort title-case of the handle; the operator can
    # rename later. This matches the canonical-name fallback that
    # ingest_source would compute anyway, so the posting rows are
    # labeled consistently whether or not the shell exists first.
    shell = TargetCompany(
        name=handle.replace("-", " ").title(),
        ats=ats,
        ats_handle=handle,
        tier=None,
    )
    session.add(shell)
    await session.flush()
    return True


async def run_broad_ingest(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> BroadIngestReport:
    """Sweep up to ``limit`` active discovered handles with the title
    pre-filter ON. See module docstring for the per-handle contract."""
    report = BroadIngestReport()

    rows = (
        (
            await session.execute(
                select(DiscoveredHandle)
                .where(DiscoveredHandle.active.is_(True))
                .order_by(DiscoveredHandle.discovered_at.asc(), DiscoveredHandle.id.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    report.handles_considered = len(rows)
    service = IngestionService()

    for dh in rows:
        if dh.ats not in _BROAD_SUPPORTED_ATS:
            report.handles_skipped_unsupported += 1
            report.per_handle.append(
                BroadIngestHandleResult(
                    ats=dh.ats, handle=dh.handle, status="skipped_unsupported_ats"
                )
            )
            continue

        # Shell company first so canonical_name + OUTER JOIN are consistent.
        if await _ensure_shell_company(session, ats=dh.ats, handle=dh.handle):
            report.shells_created += 1

        adapter = _build_adapter(dh.ats)
        try:
            async with adapter:
                run = await service.ingest_source(
                    adapter, dh.handle, session, apply_title_prefilter=True
                )
            status = str(run.status)
            fetched = run.postings_fetched
            kept = run.postings_new + run.postings_updated
        except HandleNotFoundError:
            # Benign — stale handle. Treated as an empty pull for the
            # deactivation counter. The IngestRun row records the 404
            # via ingest_source's own handler, so no extra logging here.
            status = "handle_not_found"
            fetched = 0
            kept = 0
        except Exception:
            logger.exception(
                "broad_ingest.handle_failed",
                extra={"ats": dh.ats, "handle": dh.handle},
            )
            status = "failed"
            fetched = 0
            kept = 0

        # ── Lifecycle write-back ────────────────────────────────────────────
        dh.last_ingested_at = datetime.now(tz=UTC)
        deactivated = False
        # "Empty" = nothing kept. A failed/404 run counts as empty for
        # deactivation so a permanently-dead handle eventually drops, but
        # we DON'T deactivate on a single transient failure.
        if kept == 0:
            dh.consecutive_empty_count += 1
            if dh.consecutive_empty_count >= _DEACTIVATE_AFTER_EMPTY:
                dh.active = False
                deactivated = True
                report.handles_deactivated += 1
        else:
            dh.consecutive_empty_count = 0

        report.handles_ingested += 1
        report.total_postings_fetched += fetched
        report.total_postings_kept += kept
        report.per_handle.append(
            BroadIngestHandleResult(
                ats=dh.ats,
                handle=dh.handle,
                status=status,
                postings_fetched=fetched,
                postings_kept=kept,
                deactivated=deactivated,
            )
        )

    await session.commit()
    logger.info(
        "broad_ingest.complete",
        extra={
            "considered": report.handles_considered,
            "ingested": report.handles_ingested,
            "shells_created": report.shells_created,
            "fetched": report.total_postings_fetched,
            "kept": report.total_postings_kept,
            "deactivated": report.handles_deactivated,
        },
    )
    return report


__all__ = ["BroadIngestHandleResult", "BroadIngestReport", "run_broad_ingest"]
