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
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.base import Adapter, HandleNotFoundError
from job_assist.db.enums import ATS
from job_assist.db.models import DiscoveredHandle, JobPosting, TargetCompany
from job_assist.gmail.backfill import _normalize_company
from job_assist.services.ingestion import IngestionService

logger = logging.getLogger(__name__)

# Only these ATSes are swept in the broad trial. Workday + iCIMS need
# per-tenant adapter_config (wd_number/site, or careers_url) that a
# bare discovered handle doesn't carry, so they're out of scope here.
_BROAD_SUPPORTED_ATS: frozenset[str] = frozenset({"greenhouse", "lever", "ashby"})

# Handle-health deactivation thresholds (Slice 3). A 404
# (``handle_not_found``) is a strong dead-token signal — deactivate
# fast. An empty 200 is a board that's merely between postings right
# now — deactivate slowly so a quiet week doesn't prune a live board.
# Both count the same ``consecutive_empty_count``; the threshold
# applied depends on the CURRENT pull's status. Since a dead handle
# 404s every run, its counter purely reflects 404s and trips the low
# threshold; a quiet-but-live board empties every run and trips the
# high one.
_DEACTIVATE_AFTER_NOT_FOUND = 2
_DEACTIVATE_AFTER_EMPTY = 5

# The qualified-role fit_score floor. A "qualified" role is a strong PM
# fit (the 80+ band the operator wants to review). The weekly cap
# counts these.
_QUALIFIED_SCORE_FLOOR = 80

# Default weekly cap — once this many qualified broad roles are banked
# in the current ISO week, the runner stops for the week.
_DEFAULT_WEEKLY_CAP = 100


def _current_iso_week_start() -> datetime:
    """Monday 00:00:00 UTC of the current ISO week.

    The weekly qualified cap resets on this boundary automatically —
    'this week' is derived from the wall clock each run, so there's no
    counter to reset and no cron to run.
    """
    now = datetime.now(tz=UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # ``weekday()`` is 0 for Monday.
    return midnight - timedelta(days=now.weekday())


async def count_qualified_broad_this_week(session: AsyncSession) -> int:
    """Count DISTINCT broad-shell postings that qualified this ISO week.

    'Broad shell' = a ``target_company`` with ``tier IS NULL`` (the
    curated companies all carry a pedigree tier; only broad-discovered
    shells are NULL). 'Qualified' = ``fit_score >= 80``. 'This week' =
    ``first_seen_at >= Monday 00:00 UTC``.

    Counting by ``first_seen_at`` (not ingest events) means a posting
    re-seen later in the same week is counted once, and a posting first
    seen last week never counts toward this week — so re-pulls never
    re-count. This is the weekly cap's source of truth; no separate
    counter table.
    """
    week_start = _current_iso_week_start()
    stmt = (
        select(func.count(func.distinct(JobPosting.id)))
        .select_from(JobPosting)
        .join(TargetCompany, JobPosting.target_company_id == TargetCompany.id)
        .where(TargetCompany.tier.is_(None))
        # feat/wellfound-ingest: tier IS NULL alone no longer means "broad" —
        # warm_path and wellfound shells are also tier-NULL. Scope the cap to
        # source='broad' so the query-driven Wellfound (and weekly warm-path)
        # shells don't silently consume the broad weekly quota. (Also closes
        # the warm-path-leak LOW from the June audit.)
        .where(TargetCompany.source == "broad")
        .where(JobPosting.fit_score >= _QUALIFIED_SCORE_FLOOR)
        .where(JobPosting.first_seen_at >= week_start)
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


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
    # Weekly qualified cap (Slice 3).
    weekly_cap: int = 0
    qualified_this_week_before: int = 0
    qualified_this_week_after: int = 0
    stopped_on_cap: bool = False
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
    candidate_name = handle.replace("-", " ").title()

    # feat/applied-company-tracking: a tracking-only ('applied') row may already
    # exist for this company BY NAME — it has no ats_handle, so the lookup above
    # misses it, and a fresh insert would collide on UNIQUE(name). Resolve to a
    # LINK: attach this handle/ats to the existing row (promoting it so its
    # postings join) and skip the insert.
    norm = _normalize_company(candidate_name)
    if norm:
        same_name = [
            r
            for r in (await session.execute(select(TargetCompany))).scalars().all()
            if _normalize_company(r.name) == norm
        ]
        if same_name:
            row = same_name[0]
            if row.ats_handle is None:
                row.ats = ATS(ats)
                row.ats_handle = handle
                await session.flush()
            return False

    shell = TargetCompany(
        name=candidate_name,
        ats=ats,
        ats_handle=handle,
        tier=None,
        # feat/wellfound-ingest: stamp source='broad' explicitly. The column's
        # server_default is 'curated', so a shell inserted without it was
        # mislabeled — harmless while the weekly cap keyed on tier IS NULL, but
        # now that the cap is scoped to source='broad' (so wellfound/warm_path
        # tier-NULL shells don't eat it), a broad shell MUST carry 'broad' or it
        # would silently stop counting toward its own cap.
        source="broad",
    )
    session.add(shell)
    await session.flush()
    return True


async def run_broad_ingest(
    session: AsyncSession,
    *,
    limit: int = 100,
    weekly_cap: int = _DEFAULT_WEEKLY_CAP,
) -> BroadIngestReport:
    """Sweep active discovered handles with the title pre-filter ON,
    bounded by ``limit`` handles per run AND the weekly qualified cap.

    Weekly cap (the load-bearing bound): once ``weekly_cap`` qualified
    (80+) broad roles are banked in the current ISO week, the runner
    STOPS — both at the top (if already at/over the cap, it no-ops) and
    BETWEEN boards (it checks after each board commits, before starting
    the next). The check is between boards, never mid-board, so a board
    is never half-ingested. One board can overshoot the cap (e.g. a
    board with 12 qualified roles can take us 95→107); that's accepted —
    'stop once reached', not 'exactly N'.

    Rotation: handles are ordered ``last_ingested_at ASC NULLS FIRST``
    so each run starts with never/least-recently pulled handles. Over
    successive runs the frontier advances across the full set even
    though any single run stops early on the cap.

    See module docstring for the per-handle contract.
    """
    report = BroadIngestReport(weekly_cap=weekly_cap)

    # Top-of-run cap check — if this week's quota is already banked,
    # no-op without touching any board.
    qualified_before = await count_qualified_broad_this_week(session)
    report.qualified_this_week_before = qualified_before
    report.qualified_this_week_after = qualified_before
    if qualified_before >= weekly_cap:
        report.stopped_on_cap = True
        logger.info(
            "broad_ingest.cap_already_met",
            extra={"qualified": qualified_before, "cap": weekly_cap},
        )
        return report

    rows = (
        (
            await session.execute(
                select(DiscoveredHandle)
                .where(DiscoveredHandle.active.is_(True))
                # Rotation: least-recently-ingested first (NULLS FIRST =
                # never-pulled handles lead) so the frontier advances.
                .order_by(
                    DiscoveredHandle.last_ingested_at.asc().nulls_first(),
                    DiscoveredHandle.id.asc(),
                )
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
            # Benign — stale handle. The IngestRun row records the 404
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
        # Nothing kept → bump the empty counter. The deactivation
        # threshold depends on WHY: a 404 (dead token) trips fast, an
        # empty 200 (quiet board) trips slow.
        if kept == 0:
            dh.consecutive_empty_count += 1
            threshold = (
                _DEACTIVATE_AFTER_NOT_FOUND
                if status == "handle_not_found"
                else _DEACTIVATE_AFTER_EMPTY
            )
            if dh.consecutive_empty_count >= threshold:
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

        # ── Weekly cap check (between boards) ────────────────────────────────
        # ingest_source committed this board's rows, so the count below
        # reflects them. Checking here (not mid-board) guarantees no
        # half-ingested board when we stop.
        qualified_now = await count_qualified_broad_this_week(session)
        if qualified_now >= weekly_cap:
            report.stopped_on_cap = True
            logger.info(
                "broad_ingest.cap_reached",
                extra={"qualified": qualified_now, "cap": weekly_cap},
            )
            break

    report.qualified_this_week_after = await count_qualified_broad_this_week(session)
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
            "qualified_this_week": report.qualified_this_week_after,
            "weekly_cap": report.weekly_cap,
            "stopped_on_cap": report.stopped_on_cap,
        },
    )
    return report


__all__ = [
    "BroadIngestHandleResult",
    "BroadIngestReport",
    "count_qualified_broad_this_week",
    "run_broad_ingest",
]
