"""FastAPI application entry point."""

from __future__ import annotations

import hmac
import logging
import os
import traceback
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Text, and_, cast, true
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import RequestResponseEndpoint

from job_assist.config import settings
from job_assist.db.enums import ActionType
from job_assist.db.session import get_db
from job_assist.schemas.contact import ContactCreate, ContactUpdate
from job_assist.schemas.embeddings import (
    EmbeddingRetryResponse,
    EmbeddingSweepResponse,
    NearestResponse,
)
from job_assist.schemas.operator_profile import OperatorProfileUpdate
from job_assist.schemas.outreach import OutreachMessageCreate
from job_assist.schemas.public import (
    DEFAULT_SORT,
    ApplicationStatusUpdate,
    BulkPostingStateRequest,
    PostingStateRequest,
    SortKey,
)
from job_assist.schemas.reclassify import ReclassifySweepRequest, ReclassifySweepResponse
from job_assist.schemas.resume_version import ResumeVersionCreate
from job_assist.schemas.score import ScoreSweepRequest, ScoreSweepResponse

logger = structlog.get_logger(__name__)


def _schema_guard_enabled() -> bool:
    """Whether the startup schema guard runs (feat/migration-deploy-gate).

    On in production (the deploy hole that caused #104/#107). Overridable via
    SCHEMA_GUARD=strict (force on, e.g. CI) / SCHEMA_GUARD=off (force off, an
    escape hatch). Off by default in dev/test so an unmigrated local DB doesn't
    block boot.
    """
    flag = os.getenv("SCHEMA_GUARD", "").strip().lower()
    if flag == "strict":
        return True
    if flag == "off":
        return False
    return settings.environment == "production"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    logger.info("api.startup", environment=settings.environment, version="0.0.1")
    # Layer 2 of the migration-deploy gate: refuse to serve if the live DB
    # schema is behind the code's migration head. A raise here aborts startup
    # so the deploy fails and the prior healthy version keeps running — instead
    # of serving 500s against a stale schema (the #104 / #107 failure mode).
    # Layer 1 (scripts/start.sh) normally guarantees this passed by running
    # `alembic upgrade head` before uvicorn; this catches an entrypoint bypass.
    if _schema_guard_enabled():
        from job_assist.db.schema_guard import assert_schema_at_head
        from job_assist.db.session import engine

        await assert_schema_at_head(engine)
        logger.info("api.schema_guard.ok")
    yield
    logger.info("api.shutdown")


app = FastAPI(
    title="Job Assist API",
    description="Personal job-search aggregation and triage system",
    version="0.0.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth gate (feat/api-auth) ───────────────────────────────────────────────
# A single shared bearer token gates EVERY route except /health. The app is
# single-operator; this closes the world-readable exposure of PII + history +
# mutating /admin/* endpoints. The frontend sends the token via a Next.js
# server-side proxy (never the browser); the GitHub Actions crons send it via
# the API_AUTH_TOKEN secret.
#
# Rollout safety: defaults to WARN-ONLY (``AUTH_ENFORCE`` unset/false) — it
# LOGS missing/invalid tokens but lets requests through, so every client can be
# wired to send the token before enforcement. Flip ``AUTH_ENFORCE=true`` only
# after the warn-logs confirm all clients authenticate.

# Only /health stays open: Railway's healthcheck, the startup schema guard, and
# the crons' pre-check curl it. Everything else — including /openapi.json,
# /docs, /redoc, and / — is gated (the frontend ships a committed openapi
# snapshot, so it needs no runtime access to the live schema).
_AUTH_ALLOWLIST = frozenset({"/health"})


def _extract_bearer(authorization: str) -> str:
    """Return the token from an ``Authorization: Bearer <token>`` header, or ''."""
    prefix = "bearer "
    if authorization[: len(prefix)].lower() == prefix:
        return authorization[len(prefix) :].strip()
    return ""


@app.middleware("http")
async def auth_guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Gate every route except /health behind the shared bearer token.

    WARN mode (default): log missing/invalid, allow through. ENFORCE mode
    (``auth_enforce``): 401 on missing/invalid. If the token is UNCONFIGURED
    (empty env var), fail OPEN with a critical log rather than brick the app —
    the rollout provisions the token before flipping enforce.
    """
    path = request.url.path
    # CORS preflight carries no Authorization header — let the CORS middleware
    # answer OPTIONS. /health is the one always-open route.
    if request.method == "OPTIONS" or path in _AUTH_ALLOWLIST:
        return await call_next(request)

    expected = settings.api_auth_token
    provided = _extract_bearer(request.headers.get("authorization", ""))
    # fix(audit): compare BYTES. hmac.compare_digest raises TypeError on str
    # operands containing non-ASCII — a garbage/multibyte bearer token (one
    # curl typo away) 500'd every request instead of failing auth cleanly.
    # UTF-8 encoding is total, so the comparison itself can never raise.
    valid = bool(expected) and hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    )

    if not valid:
        if not expected:
            # Misconfiguration: enforce requested but no token set. Fail OPEN
            # (loud) so a bad deploy can't silently brick every client.
            logger.critical("auth.unconfigured", method=request.method, path=path)
        else:
            logger.warning(
                "auth.missing_or_invalid",
                method=request.method,
                path=path,
                enforce=settings.auth_enforce,
            )
        if settings.auth_enforce and expected:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    # Catch downstream exceptions HERE, inside the middleware, not only via the
    # global @app.exception_handler — a bare-Exception handler doesn't reliably
    # fire through BaseHTTPMiddleware (Starlette composition gotcha), which is
    # how a failing DB write returned an opaque empty-body 500. This guarantees a
    # catchable error is logged (``unhandled_exception_mw``, full traceback) AND
    # echoed in the response body. A worker SIGKILL/segfault still bypasses this —
    # the body then STAYS empty, which is itself the signal (a crash, not a
    # catchable DB error).
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error(
            "unhandled_exception_mw",
            method=request.method,
            path=path,
            error_type=type(exc).__name__,
            error=str(exc)[:1000],
            traceback=traceback.format_exc()[:4000],
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            },
        )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Surface the REAL error on any otherwise-unhandled 500.

    Previously an unhandled exception (e.g. a DB write failing) returned a bare,
    opaque 500 with an empty body — leaving write failures undiagnosable. This
    logs the full traceback (greppable in the Railway logs as
    ``unhandled_exception``) AND echoes the exception type + message in the
    response body, so a failing write can be diagnosed straight from the HTTP
    response (``curl`` it) without digging through logs. The exception MESSAGE is
    what decides the cause — e.g. asyncpg's "could not extend file" (disk),
    "read-only transaction" (replica routing), "connection refused" /
    "terminating connection" (DB restart → stale pool), "too many connections"
    (pool exhaustion).

    The app is single-operator behind the bearer gate, so echoing the error text
    is acceptable and the diagnostic value is high. FastAPI handles
    ``HTTPException`` before this, so 4xx responses are unaffected.
    """
    logger.error(
        "unhandled_exception",
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
        error=str(exc)[:1000],
        traceback=traceback.format_exc()[:4000],
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        },
    )


DbSession = Annotated[AsyncSession, Depends(get_db)]

# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok", "version": "0.0.1"}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"name": "job-assist-api", "version": "0.0.1"}


@app.get("/admin/auth-status")
async def auth_status() -> dict[str, bool]:
    """Auth-rollout diagnostic (feat/api-auth) — BOOL ONLY, never the token.

    Confirms, without reading logs and without flipping enforce:
      * ``token_configured`` — whether ``API_AUTH_TOKEN`` actually loaded into
        THIS running build (the thing warn-mode behavior can't reveal, since a
        valid token is logged silently and a wrong/missing one behaves the same
        as a right one until enforce is on).
      * ``enforce`` — the live ``AUTH_ENFORCE`` flag.

    Deliberately a GATED route (NOT in the /health allowlist): hitting it
    without a token in warn mode returns the bools AND emits a fresh
    ``auth.missing_or_invalid`` log line — proving the middleware is evaluating.
    After enforce flips, it requires the token like every other route.

    Never returns the secret value — only whether one is present.
    """
    return {
        "token_configured": bool(settings.api_auth_token),
        "enforce": settings.auth_enforce,
    }


# ── Admin — ingestion ─────────────────────────────────────────────────────────


# ATS sources the daily cron knows how to ingest. Workday joined the
# set in PR #33; iCIMS in PR #55.
#
# TODO(adapter-dispatch-drift): The ATS vocabulary is currently
# duplicated across three sites — this constant, ``_SUPPORTED`` in the
# ingest-trigger handler below, and ``_SUPPORTED_ATS`` in cli.py. Each
# adapter PR pays the copy-paste cost three times. A future adapter PR
# (PR #56+) should consider promoting this into a single registry —
# e.g. ``adapters/__init__.py::ADAPTERS = {"greenhouse": Greenhouse, …}``
# — that all three sites read from. Out of scope for PR #55 per the
# strip-philosophy "no base-class refactor" rule.
_INGESTABLE_ATS = ("greenhouse", "lever", "ashby", "workday", "icims")

# chore/drop-workday-icims-direct-plan: the DAILY PLAN's adapter set. Workday and
# iCIMS are deliberately EXCLUDED from the direct daily fetch: their boards block
# Railway's egress IP, so a direct fetch always returns 0 (a guaranteed no-op
# that just adds a pointless call). Those curated employers are sourced instead
# by the Apify Fantastic.jobs path (services/fantastic_ingest.FANTASTIC_SOURCED_ATS
# = workday/icims, targeting by domain), which runs as its own daily cron step.
# _INGESTABLE_ATS stays the FULL set so the /postings ats filter
# (_ALLOWED_ATS_VALUES) can still surface the Apify-sourced workday/icims
# postings; only the direct PLAN query narrows to the three free boards that
# actually fetch from Railway.
_DIRECT_PLAN_ATS = ("greenhouse", "lever", "ashby")


@app.get("/admin/ingest/plan")
async def get_ingest_plan(db: DbSession) -> list[dict[str, str]]:
    """List ``(ats, handle)`` pairs the daily *curated* cron should ingest.

    Filters to rows where:
      * ``ats`` is one of the three free direct boards (``_DIRECT_PLAN_ATS`` =
        greenhouse/lever/ashby). workday/icims are EXCLUDED — their boards block
        Railway's egress IP (direct fetch = guaranteed 0), so they're sourced by
        the Apify Fantastic path instead, not the direct daily plan.
      * ``ats_handle IS NOT NULL`` (we can't ingest without a handle)
      * ``tier IS NOT NULL`` — the curated/broad separation (Slice 2).
        Curated companies carry a hand-assigned pedigree tier (1-4);
        broad-discovered shells (``services/broad_ingest.py``) have
        ``tier=NULL``. The daily cron ingests ONLY curated companies
        WITHOUT the title pre-filter; broad shells are swept separately
        by ``POST /admin/broad-ingest/run`` WITH the filter. Omitting
        this clause would pull the broad shells into the unfiltered
        daily cron, flooding the DB with the non-PM long tail this
        whole effort exists to avoid.
      * ``source IN ('curated','broad')`` — a POSITIVE provenance
        allowlist (fix/plan-source-filter). Rows reactivated into other
        cohorts can keep a leftover tier+handle: Athene (``warm_path``)
        leaked into this plan as a guaranteed-zero free-adapter fetch — its
        board blocks our egress IP, which is exactly why it lives on the
        weekly Apify sweep. ``deactivated``/``applied`` are excluded the
        same way. ``broad`` stays allowed: promoting a broad shell sets its
        tier while source stays 'broad' by contract.
      * No active ``closed_channel`` row exists for the target_company
        (``unsealed_at IS NULL`` denotes "currently sealed")

    Ordered by ``tier ASC, name ASC`` so Tier-1 companies ingest first
    and the most-important data lands even if later runs in the same
    cron invocation fail.

    Schema note: spec sketched ``target_company.is_closed_channel`` as a
    boolean column. Closed-channel state already lives in its own table
    (single source of truth, with an ``unsealed_at`` audit field) —
    denormalising it onto ``target_company`` would create drift between
    two stores. Same pattern as PR #23's hard-rule filter.
    """
    from sqlalchemy import select

    from job_assist.db.models.closed_channel import ClosedChannel
    from job_assist.db.models.target_company import TargetCompany

    active_closed = (
        select(ClosedChannel.id)
        .where(ClosedChannel.target_company_id == TargetCompany.id)
        .where(ClosedChannel.unsealed_at.is_(None))
        .exists()
    )

    rows = (
        await db.execute(
            select(TargetCompany.ats, TargetCompany.ats_handle)
            # _DIRECT_PLAN_ATS, NOT _INGESTABLE_ATS: workday/icims boards block
            # Railway's egress IP (direct fetch = guaranteed 0), so they're
            # sourced by the Apify Fantastic path instead and excluded from the
            # direct daily plan. _INGESTABLE_ATS stays full for the /postings
            # filter so Apify-sourced workday/icims postings remain filterable.
            .where(TargetCompany.ats.in_(_DIRECT_PLAN_ATS))
            .where(TargetCompany.ats_handle.isnot(None))
            # Curated only — exclude broad-ingest shells (tier IS NULL).
            .where(TargetCompany.tier.isnot(None))
            # fix/plan-source-filter: POSITIVE source allowlist. The old
            # ``source != 'applied'`` let any other-cohort row with a leftover
            # tier+handle ride the daily plan: Athene (reactivated as
            # ``warm_path``, kept tier=2 + handle from its carrier days)
            # leaked in as a guaranteed-zero free-adapter fetch — its board
            # blocks our egress IP, which is exactly why it lives on the
            # weekly Apify sweep. ``broad`` stays IN the allowlist because
            # promoting a broad shell (crawl-config sets tier, source stays
            # 'broad' by contract — test_promote_broad_shell_into_plan) is
            # the documented way into this plan; un-promoted shells are
            # already excluded by the tier guard above.
            .where(TargetCompany.source.in_(("curated", "broad")))
            .where(~active_closed)
            .order_by(TargetCompany.tier.asc(), TargetCompany.name.asc())
        )
    ).all()

    return [{"ats": str(ats), "handle": str(handle)} for ats, handle in rows]


@app.post("/admin/ingest/{ats}/{handle}")
async def trigger_ingest(
    ats: str,
    handle: str,
    db: DbSession,
) -> dict[str, Any]:
    """Trigger an ingestion run for one ATS / handle combination.

    Returns the IngestRun ID and initial status.  The run executes
    synchronously within the request; a background-task variant can be
    added later when latency matters.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from sqlalchemy import select

    from job_assist.adapters.ashby import AshbyAdapter
    from job_assist.adapters.base import Adapter
    from job_assist.adapters.greenhouse import GreenhouseAdapter
    from job_assist.adapters.icims import ICIMSAdapter
    from job_assist.adapters.lever import LeverAdapter
    from job_assist.adapters.workday import WorkdayAdapter
    from job_assist.db.models.target_company import TargetCompany
    from job_assist.services.ingestion import IngestionService

    # Keep in sync with ``_INGESTABLE_ATS`` above and ``_SUPPORTED_ATS`` in
    # cli.py — see the TODO(adapter-dispatch-drift) tag.
    _SUPPORTED = {"greenhouse", "lever", "ashby", "workday", "icims"}
    if ats not in _SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported ATS {ats!r}. Supported: {sorted(_SUPPORTED)}",
        )

    adapter: Adapter
    if ats == "greenhouse":
        adapter = GreenhouseAdapter()
    elif ats == "lever":
        adapter = LeverAdapter()
    elif ats == "ashby":
        adapter = AshbyAdapter()
    elif ats == "workday":
        # Workday's URL needs the tenant's wd_number + site shard, which
        # live on `target_company.adapter_config` (PR #33). Look them up
        # by the handle the caller passed.
        tc_row = (
            await db.execute(
                select(TargetCompany).where(
                    TargetCompany.ats == "workday",
                    TargetCompany.ats_handle == handle,
                )
            )
        ).scalar_one_or_none()
        if tc_row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No target_company with ats='workday' and "
                    f"ats_handle={handle!r}. Seed via SQL with adapter_config."
                ),
            )
        cfg = tc_row.adapter_config or {}
        if not isinstance(cfg, dict) or "wd_number" not in cfg or "site" not in cfg:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"target_company {handle!r} is missing adapter_config keys "
                    f"`wd_number` and `site`."
                ),
            )
        adapter = WorkdayAdapter(adapter_config=cfg)
    elif ats == "icims":
        # PR #55: iCIMS adapter_config is OPTIONAL — the default URL
        # ``https://careers-<handle>.icims.com`` works for the majority
        # of tenants. Look up the row only to surface a useful 404 when
        # the handle isn't registered, AND to forward ``adapter_config``
        # (which may carry a ``careers_url`` override for tenants with
        # non-default URLs).
        tc_row = (
            await db.execute(
                select(TargetCompany).where(
                    TargetCompany.ats == "icims",
                    TargetCompany.ats_handle == handle,
                )
            )
        ).scalar_one_or_none()
        if tc_row is None:
            raise HTTPException(
                status_code=404,
                detail=(f"No target_company with ats='icims' and ats_handle={handle!r}."),
            )
        icims_cfg = tc_row.adapter_config if isinstance(tc_row.adapter_config, dict) else None
        adapter = ICIMSAdapter(adapter_config=icims_cfg)
    else:
        # Unreachable given the guard above, but keeps mypy happy.
        raise HTTPException(status_code=400, detail=f"ATS {ats!r} not yet implemented")

    service = IngestionService()
    async with adapter:
        run = await service.ingest_source(adapter, handle, db)

    return {
        "ingest_run_id": str(run.id),
        "status": run.status,
        "postings_fetched": run.postings_fetched,
        "postings_new": run.postings_new,
        "postings_updated": run.postings_updated,
    }


# Cohorts the Apify path may sweep. ``curated`` = the daily cron's set;
# ``warm_path`` = alumni-network employers (feat/warm-path-ingest), swept
# WEEKLY by warm-path-ingest.yml. Disjoint by construction — see
# list_fantastic_targets.
_FANTASTIC_SOURCES = {"curated", "warm_path"}

# Title-filter tracks the probe may exercise (feat/strategy-spine): the
# curated PM/PO band vs the warm-path PM/PO + strategy-family band.
_FANTASTIC_TRACKS = {"pm", "strategy"}


@app.post("/admin/ingest/fantastic", tags=["admin"])
async def trigger_fantastic_ingest(db: DbSession, source: str = "curated") -> dict[str, Any]:
    """Ingest ONE cohort of Workday/iCIMS employers via the Fantastic.jobs
    Apify actor (feat/fantastic-jobs-ingest + feat/warm-path-ingest).

    Those boards block Railway's datacenter egress IP, so the free Workday/iCIMS
    adapters fetch 0 — Apify's infra crawls them instead. The PM/PO title filter
    is applied at the API call (a few jobs/employer = pennies/day).
    ``?source=curated`` (default) is the daily cron's set; ``?source=warm_path``
    is the weekly alumni-network sweep. greenhouse/lever/ashby stay on the free
    adapters. Each employer is its own ingest_run; returns per-employer counts.

    503 when ``APIFY_API_TOKEN`` is unset (server-side only — Railway env +
    GitHub secret; never client-exposed).
    """
    from job_assist.services.fantastic_ingest import ingest_curated_via_fantastic

    if source not in _FANTASTIC_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of {sorted(_FANTASTIC_SOURCES)}",
        )
    token = settings.apify_api_token
    if not token:
        raise HTTPException(
            status_code=503,
            detail="APIFY_API_TOKEN is not configured (server-side Apify credential).",
        )
    return await ingest_curated_via_fantastic(db, token, source=source)


# Wellfound query roles the operator may sweep. URL-slug form (the actor is
# URL-driven). Kept small + explicit so a typo can't fan out into many paid
# queries; the default targets where first-PM-hire/0-to-1 roles concentrate.
_WELLFOUND_ROLES = {"product-manager", "product-management", "founding-product-manager"}


@app.post("/admin/ingest/wellfound", tags=["admin"])
async def trigger_wellfound_ingest(
    db: DbSession,
    role: str = "product-manager",
    only_remote: bool = True,
    page_limit: int = 1,
    monitor_mode: bool = False,
) -> dict[str, Any]:
    """Query Wellfound for one role via the clearpath Apify actor, discover
    companies from the postings, and ingest each through the standard pipeline
    (feat/wellfound-ingest). Query-driven — discovered companies are
    ``source='wellfound'`` shells that NEVER join a recurring plan.

    HARD COST CAPS: ``page_limit`` is clamped (1-5) and the actor call carries a
    ``_MAX_RECORDS_PER_RUN`` failsafe + a per-run cost-sanity alert, so a filter
    regression can never fail open into an unbounded paid fetch. ``monitor_mode``
    fetches only-new since the last run (cheaper at a daily cadence; verify its
    behavior on a second pull before relying on it).

    Returns the Gate-1 readout: fetched / kept / skipped_quality, the estimated
    run cost + cost-guard flag, per-company new/updated counts. 503 when
    ``APIFY_API_TOKEN`` is unset.
    """
    from job_assist.services.wellfound_ingest import ingest_wellfound

    if role not in _WELLFOUND_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role must be one of {sorted(_WELLFOUND_ROLES)}",
        )
    if not 1 <= page_limit <= 5:
        raise HTTPException(status_code=422, detail="page_limit must be 1..5 (hard cost cap)")
    token = settings.apify_api_token
    if not token:
        raise HTTPException(
            status_code=503,
            detail="APIFY_API_TOKEN is not configured (server-side Apify credential).",
        )
    return await ingest_wellfound(
        db,
        token,
        role=role,
        only_remote=only_remote,
        page_limit=page_limit,
        monitor_mode=monitor_mode,
    )


@app.get("/admin/ingest/fantastic-plan", tags=["admin"])
async def get_fantastic_plan(db: DbSession, source: str = "curated") -> dict[str, Any]:
    """List the employers ONE Apify cohort would sweep — the fantastic-path
    analog of ``GET /admin/ingest/plan`` (which covers only the free adapters).

    Read-only; used to verify the warm-path cohort after seeding without
    spending an Apify call.
    """
    from job_assist.services.fantastic_ingest import apify_domain_for, list_fantastic_targets

    if source not in _FANTASTIC_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of {sorted(_FANTASTIC_SOURCES)}",
        )
    targets = await list_fantastic_targets(db, source=source)
    return {
        "source": source,
        "count": len(targets),
        "companies": [
            {
                "name": tc.name,
                "ats": tc.ats.value if hasattr(tc.ats, "value") else str(tc.ats),
                "domain": tc.domain,
                "apify_domain": apify_domain_for(tc),
                "last_swept_at": tc.last_swept_at.isoformat() if tc.last_swept_at else None,
            }
            for tc in targets
        ],
    }


# Single-segment path (``fantastic-probe``, not ``fantastic/probe``) so it
# isn't captured by the earlier ``/admin/ingest/{ats}/{handle}`` route — a
# two-segment ``fantastic/probe`` matches that catch-all as ats='fantastic'.
@app.post("/admin/ingest/fantastic-probe", tags=["admin"])
async def probe_fantastic(
    domain: str, limit: int = 5, title_filter: bool = False, track: str = "pm"
) -> dict[str, Any]:
    """Diagnostic Apify pull for one employer ``domain`` — count + sample titles
    + the first record's ``field_keys``/``sample_record``, NO persist.

    ``title_filter=false`` (default) drops the PM/PO filter to tell "no PM/PO
    roles here" from "domain targeting off" when the filtered ingest returns 0.
    ``title_filter=true`` keeps the filter (a known-valid query) to fetch a real
    matching record for field inspection. An Apify HTTP error is surfaced
    (status + body), not swallowed into a 500. 503 if the token is unset;
    ``limit`` capped at 50."""
    from job_assist.services.fantastic_ingest import probe_fantastic_domain

    token = settings.apify_api_token
    if not token:
        raise HTTPException(status_code=503, detail="APIFY_API_TOKEN is not configured.")
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=422, detail="limit must be 1..50")
    if track not in _FANTASTIC_TRACKS:
        raise HTTPException(
            status_code=422, detail=f"track must be one of {sorted(_FANTASTIC_TRACKS)}"
        )
    return await probe_fantastic_domain(
        token, domain=domain, limit=limit, title_filter=title_filter, track=track
    )


@app.post("/admin/postings/mark-stale", tags=["admin"])
async def mark_stale_postings_endpoint(
    db: DbSession,
    stale_after_days: int = 7,
) -> dict[str, int]:
    """Mark postings stale (set ``closed_at``) when not seen on their ATS
    for ``stale_after_days``.

    Called by the daily ingest cron AFTER the per-board loop completes, so
    every still-live posting has had its ``last_seen_at`` refreshed first.
    Also serves as the one-time backfill (call once after deploy to close
    the existing stale tail). Idempotent — already-closed rows are skipped.

    Bestiary 5.18: ``closed_at`` is the lifecycle column; this is its
    writer, ``GET /postings`` (default ``closed_at IS NULL``) its reader.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from job_assist.services.ingestion import mark_stale_postings

    if stale_after_days < 1:
        raise HTTPException(status_code=422, detail="stale_after_days must be >= 1")
    marked = await mark_stale_postings(db, stale_after_days=stale_after_days)
    return {"marked_stale": marked, "stale_after_days": stale_after_days}


@app.post("/admin/postings/reeval-hard-rules", tags=["admin"])
async def reeval_hard_rules_endpoint(db: DbSession) -> dict[str, Any]:
    """Re-evaluate ``apply_hard_rules`` across all OPEN postings and rewrite
    ``hard_rule_failed`` + ``hard_rules_evaluated_at`` (PR C).

    Run this after changing the salary floor/ceiling (or any hard-rule knob)
    in Settings (``PUT /operator/profile``) so existing postings reflect the
    new thresholds — ingest only evaluates rows as they arrive. Also doubles
    as the one-time backfill: call once after deploy to populate the column
    for the pre-existing corpus.

    Only open postings are evaluated (``closed_at IS NULL``) — composes with
    the same filter ``GET /postings`` applies, and there's no point scoring a
    removed posting. Pure function, no LLM/network; one pass + one commit.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from sqlalchemy import select

    from job_assist.db.models import ClosedChannel, JobPosting, OperatorProfile, TargetCompany
    from job_assist.triage.config import hard_rule_config_from_profile
    from job_assist.triage.hard_rules import apply_hard_rules

    op_row = await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    operator_profile = op_row.scalar_one_or_none()
    if operator_profile is None:
        raise HTTPException(
            status_code=400,
            detail="operator_profile is unseeded; cannot evaluate hard rules",
        )
    config = hard_rule_config_from_profile(operator_profile)

    # Preload active (sealed) closed-channel rows into a dict keyed by
    # target_company_id — one query, no per-posting lookup (avoids N+1).
    cc_rows = (
        (await db.execute(select(ClosedChannel).where(ClosedChannel.unsealed_at.is_(None))))
        .scalars()
        .all()
    )
    closed_by_company: dict[Any, ClosedChannel] = {
        cc.target_company_id: cc for cc in cc_rows if cc.target_company_id is not None
    }

    # Open postings + their tier-bearing company (OUTER JOIN — postings with
    # no matched company still get evaluated; target_company=None is valid).
    rows = (
        await db.execute(
            select(JobPosting, TargetCompany)
            .outerjoin(TargetCompany, JobPosting.target_company_id == TargetCompany.id)
            .where(JobPosting.closed_at.is_(None))
        )
    ).all()

    now = datetime.now(tz=UTC)
    evaluated = 0
    passed = 0
    by_rule: dict[str, int] = {}
    for posting, target_company in rows:
        verdict = apply_hard_rules(
            posting,
            target_company,
            closed_by_company.get(posting.target_company_id),
            config,
        )
        posting.hard_rule_failed = None if verdict.passed else verdict.failed_rule
        posting.hard_rules_evaluated_at = now
        evaluated += 1
        if verdict.passed:
            passed += 1
        else:
            by_rule[verdict.failed_rule] = by_rule.get(verdict.failed_rule, 0) + 1

    await db.commit()
    return {
        "evaluated": evaluated,
        "passed": passed,
        "failed": evaluated - passed,
        "by_rule": by_rule,
    }


@app.post("/admin/postings/reparse-salary", tags=["admin"])
async def reparse_salary_endpoint(db: DbSession) -> dict[str, Any]:
    """Re-run ``parse_compensation`` on ``jd_text`` for open Greenhouse-sourced
    postings and **overwrite** salary (correction backfill).

    The salary text-mining lives in the Greenhouse adapter (PR #80 — Ashby
    feeds the parser a clean compensationTierSummary, no body mining). The
    parser fix in PR #88 (range ordering, multi-currency scoping, magnitude +
    ratio sanity) corrects future parses, but the ingest self-heal is
    fill-if-NULL by design (test_salary_self_heal_never_overwrites_existing
    pins that), so existing rows with non-NULL WRONG salary (the 18 inverted
    ranges + the $142M-style garbage) stay wrong without this endpoint.

    Behaviour:
      * Targets open postings (``closed_at IS NULL``) whose source ats is
        ``greenhouse`` (EXISTS subquery on PostingSource — a posting can
        carry multiple source rows; we just need any to match).
      * Writes only when the new parse DIFFERS from the stored value, so
        already-correct rows don't churn.
      * When the new parse returns ``None`` (rejected by the magnitude or
        range-ratio guards — e.g. the ``$142,400,000`` garble), salary is
        **set to NULL** rather than leaving the old garbage in place.

    Re-run ``POST /admin/postings/reeval-hard-rules`` after this to recompute
    ``salary_floor``/``salary_ceiling`` failures against the corrected values.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from sqlalchemy import exists, select

    from job_assist.adapters.normalization import parse_compensation
    from job_assist.db.models import JobPosting, PostingSource

    greenhouse_source = exists(
        select(PostingSource.id).where(
            PostingSource.job_posting_id == JobPosting.id,
            PostingSource.ats == "greenhouse",
        )
    )
    rows = (
        (
            await db.execute(
                select(JobPosting).where(JobPosting.closed_at.is_(None)).where(greenhouse_source)
            )
        )
        .scalars()
        .all()
    )

    evaluated = 0
    changed = 0
    inversions_fixed = 0
    rejected_to_null = 0

    for p in rows:
        evaluated += 1
        new_min, new_max, new_currency, new_period = parse_compensation(p.jd_text or "")
        new_period_str = new_period or "unknown"

        old_min, old_max = p.salary_min, p.salary_max
        was_inverted = old_min is not None and old_max is not None and old_min > old_max
        # salary_period is a SalaryPeriod enum on DB-loaded rows; compare on str.
        old_period_str = str(getattr(p.salary_period, "value", p.salary_period))

        if (
            new_min == old_min
            and new_max == old_max
            and new_currency == p.salary_currency
            and new_period_str == old_period_str
        ):
            continue  # already correct — no churn

        p.salary_min = new_min
        p.salary_max = new_max
        p.salary_currency = new_currency
        # ``salary_period`` is a non-null enum column; SQLAlchemy coerces the
        # string value on assignment (same pattern the ingest update branch
        # uses for self-heal).
        p.salary_period = new_period_str  # type: ignore[assignment]
        changed += 1
        if new_min is None:
            rejected_to_null += 1
        if was_inverted and new_min is not None and new_max is not None and new_min <= new_max:
            inversions_fixed += 1

    await db.commit()
    return {
        "evaluated": evaluated,
        "changed": changed,
        "inversions_fixed": inversions_fixed,
        "rejected_to_null": rejected_to_null,
    }


# ── Admin — discover-ats batch ────────────────────────────────────────────────


@app.post("/admin/discover-ats/run")
async def discover_ats_run(
    db: DbSession,
    commit: bool = False,
) -> dict[str, Any]:
    """Probe every ``target_company`` where ``ats='unknown'`` and report matches.

    Dry-run by default.  Pass ``?commit=true`` to also write the detected
    ``ats`` and ``ats_handle`` back to the matched rows.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from job_assist.cli import discover_target_companies

    matched, unmatched = await discover_target_companies(db, commit=commit)
    return {
        "committed": commit,
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "matched": matched,
        "unmatched": unmatched,
    }


# ── Admin — seed target_company ───────────────────────────────────────────────


@app.post("/admin/seed/target-companies")
async def seed_target_companies(
    rows: list[dict[str, Any]],
    db: DbSession,
    backfill_nullables: bool = False,
) -> dict[str, int]:
    """Seed target_company rows from a JSON body.

    Idempotent: each row's ``name`` is checked first; existing rows are
    skipped rather than updated by default. The body is the seed JSON
    itself, so the private seed file
    (``apps/api/seeds/target_companies.json``) never needs to be
    uploaded to the Railway container — the operator runs::

        curl -X POST -H 'Content-Type: application/json' \\
             -d @apps/api/seeds/target_companies.json \\
             https://<host>/admin/seed/target-companies

    Pass ``?backfill_nullables=true`` to also patch currently-NULL
    columns on existing rows from the seed (operator-supplied values
    overwrite NULLs only — never existing non-NULL values). Used by
    feat/outcome-company-linking so the operator can hand-fill
    ``domain`` on the existing 30 rows by re-POSTing the seed.

    Returns the insert / skip / backfilled counts so the operator can
    verify the expected number of rows changed.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from job_assist.seed import seed_from_rows

    try:
        inserted, skipped, backfilled = await seed_from_rows(
            db, rows, backfill_nullables=backfill_nullables
        )
    except ValueError as exc:
        # fix(audit): 422 naming the bad field — _project_row validates the
        # ats/tier/source vocabularies now (an out-of-range tier used to
        # insert silently; a bad ats string 500'd at the SAEnum cast).
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "inserted": inserted,
        "skipped": skipped,
        "backfilled": backfilled,
        "total": inserted + skipped,
    }


_CRAWL_CONFIG_SOURCES = {"curated", "broad", "deactivated", "applied", "warm_path", "wellfound"}


def _validate_crawl_config_row(row: dict[str, Any]) -> None:
    """Validate one crawl-config patch row; raise HTTP 400 on any bad field.

    ``tier`` (when its key is present) must be null or an int 1-4; ``source``
    (when present) must be a known provenance value; ``ats`` (when present) must
    be a known ingestable ATS; ``ats_handle`` (when present) must be null or a
    string. ``bool`` is rejected as a tier because ``True``/``False`` are ints
    in Python.
    """
    if not row.get("name"):
        raise HTTPException(status_code=400, detail=f"row missing 'name': {row!r}")
    if "tier" in row:
        tier = row["tier"]
        if tier is not None and (
            not isinstance(tier, int) or isinstance(tier, bool) or not (1 <= tier <= 4)
        ):
            raise HTTPException(status_code=400, detail=f"tier must be null or 1-4: {row!r}")
    if "source" in row and row["source"] not in _CRAWL_CONFIG_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source must be one of {sorted(_CRAWL_CONFIG_SOURCES)}: {row!r}",
        )
    if "ats" in row and row["ats"] not in _ALLOWED_ATS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"ats must be one of {sorted(_ALLOWED_ATS_VALUES)}: {row!r}",
        )
    if (
        "ats_handle" in row
        and row["ats_handle"] is not None
        and not isinstance(row["ats_handle"], str)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"ats_handle must be null or a string: {row!r}",
        )


def _apply_crawl_config(tc: Any, row: dict[str, Any]) -> bool:
    """Apply a validated patch to a TargetCompany row. Patches ``tier`` /
    ``source`` / ``ats`` / ``ats_handle`` only when the key is present. Returns
    True iff a column actually changed.

    Note on ``ats``: ``tc.ats`` is a SQLAlchemy Enum, so it compares unequal to a
    raw string even when they represent the same value (``AtsKind.workday !=
    "workday"``). Compare against the underlying ``.value`` so a no-op patch is
    correctly reported ``unchanged`` rather than churning a write every call.
    """
    changed = False
    if "tier" in row and tc.tier != row["tier"]:
        tc.tier = row["tier"]
        changed = True
    if "source" in row and tc.source != row["source"]:
        tc.source = row["source"]
        changed = True
    if "ats" in row:
        current_ats = tc.ats.value if hasattr(tc.ats, "value") else tc.ats
        if current_ats != row["ats"]:
            tc.ats = row["ats"]
            changed = True
    if "ats_handle" in row and tc.ats_handle != row["ats_handle"]:
        tc.ats_handle = row["ats_handle"]
        changed = True
    return changed


@app.post("/admin/companies/crawl-config", tags=["admin"])
async def set_company_crawl_config(
    rows: list[dict[str, Any]],
    db: DbSession,
) -> dict[str, Any]:
    """Patch the crawl-controlling fields (``tier``, ``source``, ``ats``,
    ``ats_handle``) on existing ``target_company`` rows, matched by ``name``. The
    seed endpoint only inserts or backfills NULLs — it can't *change* an existing
    non-NULL value, so this is the lever for deactivating, re-tiering, or
    re-routing a company already in the DB.

    These columns gate crawling:
      * the daily curated cron (``GET /admin/ingest/plan``) ingests only rows
        with ``tier IS NOT NULL``;
      * the Apify Workday/iCIMS sweep (``list_fantastic_targets``) ingests only
        rows with ``ats IN ('workday','icims')`` AND ``source == 'curated'`` AND
        a non-NULL ``domain``.

    So to STOP crawling an off-profile company WITHOUT deleting it — preserving
    the row, its ``domain``, and all Gmail-match history — set ``tier=null`` AND
    ``source='deactivated'``: it drops out of BOTH paths and its Apify spend
    stops. To PROMOTE a broad-discovered shell (``tier=null``) into the curated
    daily cron, set ``tier`` to a pedigree (1-4). To ROUTE an ``ats='unknown'``
    employer (whose board blocks our egress IP) onto the Apify Workday path, set
    ``ats='workday'`` — the actor targets by ``domain``, so no ``ats_handle`` is
    needed. ``ats`` is mutable here precisely because ``discover-ats`` only
    detects free greenhouse/lever/ashby boards, never the IP-blocked Workday/iCIMS
    ones, leaving no other way to flip a stuck ``unknown`` row onto the paid path.

    Body: ``[{"name": "Athene", "tier": null, "source": "deactivated"}, ...]``.
    A field is patched ONLY when its key is present (JSON ``null`` => set NULL;
    an absent key leaves the column untouched). Only ``tier``, ``source``,
    ``ats``, and ``ats_handle`` are mutable here — every other column (incl.
    ``domain``) is immutable.

    Validation runs over the whole batch BEFORE any write, so a single bad value
    rejects the request with no partial commit. Idempotent — returns per-name
    updated / unchanged / not_found.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from sqlalchemy import select

    from job_assist.db.models.target_company import TargetCompany

    # Validate the entire batch up front — reject cleanly with no partial write.
    for row in rows:
        _validate_crawl_config_row(row)

    updated: list[str] = []
    unchanged: list[str] = []
    not_found: list[str] = []

    for row in rows:
        name = row["name"]
        tc = (
            await db.execute(select(TargetCompany).where(TargetCompany.name == name))
        ).scalar_one_or_none()
        if tc is None:
            not_found.append(name)
        elif _apply_crawl_config(tc, row):
            updated.append(name)
        else:
            unchanged.append(name)

    await db.commit()
    return {
        "updated": updated,
        "unchanged": unchanged,
        "not_found": not_found,
        "counts": {
            "updated": len(updated),
            "unchanged": len(unchanged),
            "not_found": len(not_found),
        },
    }


# ── Admin — seed contact ──────────────────────────────────────────────────────


@app.post("/admin/seed/contacts")
async def seed_contacts(
    rows: list[dict[str, Any]],
    db: DbSession,
) -> dict[str, int]:
    """Seed ``contact`` rows from a JSON body (PR #39).

    Same shape as ``/admin/seed/target-companies``: the body IS the seed
    payload, so the operator's private Tippie alumni JSON never has to
    land on the Railway container::

        curl -X POST -H 'Content-Type: application/json' \\
             -d @/tmp/contacts.json \\
             https://<host>/admin/seed/contacts

    Idempotent. Re-running with the same payload returns ``inserted=0``
    and ``skipped_duplicate_*`` matching the prior insert count, because
    dedup uses ``LOWER(email_primary)`` / ``LOWER(linkedin_url)`` —
    case-insensitive, matches the partial unique indexes.

    Privacy: response shape contains no names, emails, or LinkedIn URLs;
    same for the structlog line emitted by the seed service.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from job_assist.contact_seed import seed_contacts_from_rows

    response = await seed_contacts_from_rows(db, rows)
    return response.model_dump()


# ── Public — contacts list (PR #51) ───────────────────────────────────────────


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters (fix/audit): backslash first, then
    % and _ — with ``escape='\\'`` on the operator the user's input matches
    literally instead of acting as wildcards."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_ALLOWED_CONTACT_SOURCE_TYPES = frozenset(
    {"tippie_alumni", "linkedin_outreach", "recruiter_inbound", "warm_intro"}
)


async def _build_contact_filters(
    db: AsyncSession,
    *,
    source_type: list[str] | None,
    search: str | None,
    employer: str | None,
    include_archived: bool,
) -> list[Any]:
    """Shared WHERE-clause builder for the contacts list AND its CSV export
    (feat/view-exports) — one source of truth so the exported set is provably
    identical to the visible list's, same pattern as the postings export's
    shared ``build_view_parts``. Raises 422 on an unknown ``source_type``."""
    from sqlalchemy import false, func, or_, select

    from job_assist.db.models import Contact

    if source_type:
        for s in source_type:
            if s not in _ALLOWED_CONTACT_SOURCE_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=(f"source_type={s!r} not in {sorted(_ALLOWED_CONTACT_SOURCE_TYPES)}"),
                )

    where_clauses: list[Any] = []
    if not include_archived:
        where_clauses.append(Contact.archived_at.is_(None))
    if source_type:
        where_clauses.append(Contact.source_type.in_(source_type))
    # feat/warm-path-badge + fix(audit badge parity): ``?employer=Acme``
    # filters by the SAME normalizer the badge count uses
    # (company_name_match.normalize_company_name) — so the click-through
    # destination shows EXACTLY the contacts that produced "N alumni here".
    # Normalized-equality is computed in Python over the small
    # distinct-employer set (~hundreds) because the normalizer is
    # regex-based; the resulting raw strings filter in SQL so
    # COUNT/pagination stay server-side.
    if employer and employer.strip():
        from job_assist.services.company_name_match import normalize_company_name

        employer_key = normalize_company_name(employer)
        if not employer_key:
            # Normalizes to nothing (e.g. bare "Inc.") → no possible match.
            where_clauses.append(false())
        else:
            distinct_employers = (
                (
                    await db.execute(
                        select(Contact.current_employer)
                        .where(Contact.current_employer.is_not(None))
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            matching_raw = [
                raw
                for raw in distinct_employers
                if raw is not None and normalize_company_name(raw) == employer_key
            ]
            where_clauses.append(
                Contact.current_employer.in_(matching_raw) if matching_raw else false()
            )
    if search:
        # fix(audit): escape LIKE metacharacters — %, _ and \ are literal.
        pattern = f"%{_escape_like(search.strip().lower())}%"
        if pattern.strip("%"):
            # Match the name fields independently AND a "first last"
            # concatenation so "jane d" finds "Jane Doe".
            full_name = func.lower(func.concat(Contact.first_name, " ", Contact.last_name))
            where_clauses.append(
                or_(
                    func.lower(Contact.first_name).like(pattern, escape="\\"),
                    func.lower(Contact.last_name).like(pattern, escape="\\"),
                    full_name.like(pattern, escape="\\"),
                )
            )
    return where_clauses


@app.get("/contacts", tags=["public"])
async def list_contacts(
    db: DbSession,
    source_type: Annotated[list[str] | None, Query()] = None,
    search: str | None = None,
    employer: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated list of contacts for the Contacts page (PR #51).

    Default behaviour:
      * Excludes archived rows (``archived_at IS NULL``). Pass
        ``include_archived=true`` to include them. The exclusion happens
        in SQL — never in Python — so it matches the partial-UNIQUE-index
        scope on email/LinkedIn (re-ingesting an archived contact's
        email is allowed because their old row no longer occupies the
        unique slot; see migration ``e8f9a0b1c2d3``).
      * ``ORDER BY created_at DESC, id ASC`` — newest contacts first,
        stable ``id ASC`` tiebreaker on same-second creates.
      * Two queries total: one COUNT(*), one SELECT.

    Filter: ``source_type`` repeating param (``?source_type=tippie_alumni
    &source_type=linkedin_outreach``) ORs the matches.

    Search: ``?search=foo`` runs a case-insensitive substring match on
    ``first_name``, ``last_name``, and the concatenation. ILIKE is the
    right call here — it's a free-form search, not enum membership.

    TODO: add authentication before exposing publicly. The list returns
    real PII; the single-operator trust model is the only thing holding
    until proper auth lands.
    """
    from sqlalchemy import func, select

    from job_assist.db.models import Contact

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    # feat/view-exports: clause construction shared with /contacts/export.csv
    # so the export is provably identical to this list (minus pagination).
    where_clauses = await _build_contact_filters(
        db,
        source_type=source_type,
        search=search,
        employer=employer,
        include_archived=include_archived,
    )
    count_stmt = select(func.count()).select_from(Contact)
    for clause in where_clauses:
        count_stmt = count_stmt.where(clause)
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    rows_stmt = (
        select(Contact)
        .order_by(Contact.created_at.desc(), Contact.id.asc())
        .limit(limit)
        .offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)
    rows = (await db.execute(rows_stmt)).scalars().all()

    items = [
        {
            "id": str(c.id),
            "first_name": c.first_name,
            "last_name": c.last_name,
            "preferred_first_name": c.preferred_first_name,
            "email_primary": c.email_primary,
            "email_secondary": c.email_secondary,
            "linkedin_url": c.linkedin_url,
            "current_employer": c.current_employer,
            "current_position": c.current_position,
            "location_city": c.location_city,
            "location_state": c.location_state,
            "location_country": c.location_country,
            "location_metro": c.location_metro,
            "source_type": c.source_type,
            "target_company_id": str(c.target_company_id) if c.target_company_id else None,
            "archived_at": c.archived_at.isoformat() if c.archived_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in rows
    ]
    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/contacts/export.csv", tags=["public"])
async def export_contacts_csv(
    db: DbSession,
    source_type: Annotated[list[str] | None, Query()] = None,
    search: str | None = None,
    employer: str | None = None,
    include_archived: bool = False,
) -> Response:
    """Export the CURRENT FILTERED VIEW of contacts as CSV (feat/view-exports).

    Same filter vocabulary as ``GET /contacts``, built from the SAME
    ``_build_contact_filters`` helper so the exported set is provably
    identical to the visible list's — same sort (``created_at DESC, id
    ASC``), minus ``limit``/``offset``: every matching row, no cap. Zero
    matches → a valid CSV with the header row only (not an error).

    Output is RFC-4180 (CRLF, quoted cells) with a UTF-8 BOM so Excel
    opens it without mangling accented names.

    TODO: add authentication before exposing publicly — same PII trust
    model as the list endpoint (single-operator deployment).
    """
    import csv as _csv
    import io as _io

    from sqlalchemy import select

    from job_assist.db.models import Contact

    where_clauses = await _build_contact_filters(
        db,
        source_type=source_type,
        search=search,
        employer=employer,
        include_archived=include_archived,
    )

    rows_stmt = select(Contact).order_by(Contact.created_at.desc(), Contact.id.asc())
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)
    rows = (await db.execute(rows_stmt)).scalars().all()

    buf = _io.StringIO()
    writer = _csv.writer(buf, lineterminator="\r\n")
    writer.writerow(
        [
            "first_name",
            "preferred_first_name",
            "last_name",
            "current_position",
            "current_employer",
            "source_type",
            "email_primary",
            "email_secondary",
            "linkedin_url",
            "archived_at",
            "added",
        ]
    )
    for c in rows:
        writer.writerow(
            [
                c.first_name,
                c.preferred_first_name or "",
                c.last_name,
                c.current_position or "",
                c.current_employer or "",
                c.source_type,
                c.email_primary or "",
                c.email_secondary or "",
                c.linkedin_url or "",
                c.archived_at.isoformat() if c.archived_at else "",
                c.created_at.date().isoformat(),
            ]
        )

    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="contacts-export-{stamp}.csv"',
        },
    )


# ── Contact CRUD + outreach (PR #52) ──────────────────────────────────────────


def _contact_detail_dict(c: Any) -> dict[str, Any]:
    """Serialize a Contact ORM row to the ``ContactDetail`` wire shape.

    Shared by ``GET /contacts/{id}``, ``POST /contacts``, and
    ``PATCH /contacts/{id}`` so the responses are byte-identical.
    """
    return {
        "id": str(c.id),
        "first_name": c.first_name,
        "last_name": c.last_name,
        "preferred_first_name": c.preferred_first_name,
        "email_primary": c.email_primary,
        "email_secondary": c.email_secondary,
        "linkedin_url": c.linkedin_url,
        "phone": c.phone,
        "current_employer": c.current_employer,
        "current_position": c.current_position,
        "location_city": c.location_city,
        "location_state": c.location_state,
        "location_country": c.location_country,
        "location_metro": c.location_metro,
        "source_type": c.source_type,
        "source_metadata": c.source_metadata,
        "job_functions_of_interest": c.job_functions_of_interest,
        "industries_of_interest": c.industries_of_interest,
        "contact_opt_in": c.contact_opt_in,
        "contact_opt_in_topics": c.contact_opt_in_topics,
        "notes": c.notes,
        "target_company_id": str(c.target_company_id) if c.target_company_id else None,
        "archived_at": c.archived_at.isoformat() if c.archived_at else None,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _outreach_message_dict(m: Any) -> dict[str, Any]:
    """Serialize an OutreachMessage ORM row to the wire shape.

    ORM attribute is ``message_metadata`` (SQLAlchemy reserves
    ``metadata`` on Base); wire JSON is ``metadata``.
    """
    return {
        "id": str(m.id),
        "contact_id": str(m.contact_id),
        "direction": m.direction,
        "channel": m.channel,
        "subject": m.subject,
        "body": m.body,
        "sent_at": m.sent_at.isoformat(),
        "posting_id": str(m.posting_id) if m.posting_id else None,
        "source": m.source,
        "external_message_id": m.external_message_id,
        "metadata": m.message_metadata,
        "created_at": m.created_at.isoformat(),
    }


@app.post("/contacts", tags=["public"], status_code=201)
async def create_contact(
    payload: ContactCreate,
    db: DbSession,
) -> dict[str, Any]:
    """Operator-driven contact create (PR #52).

    Distinct from ``POST /admin/seed/contacts`` (xlsx ingest, count-only
    response). This one returns a full ContactDetail so the frontend
    can immediately render the row in the detail panel.

    Returns 422 on:
      * Pydantic validation failures (missing channel, bad source_type,
        empty name, …) — handled by FastAPI's normal flow.
      * UNIQUE-index conflict on ``LOWER(email_primary)`` or
        ``LOWER(linkedin_url)`` among active rows — caught here and
        re-raised with a clean message instead of an opaque 500.

    Returns 404 on unknown ``target_company_id``.

    TODO: add authentication. Single-operator trust model for now.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from job_assist.db.models import Contact, TargetCompany

    if payload.target_company_id is not None:
        exists = (
            await db.execute(
                select(TargetCompany.id).where(TargetCompany.id == payload.target_company_id),
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"target_company {payload.target_company_id} not found",
            )

    row = Contact(**payload.model_dump())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The two partial UNIQUE indexes both LOWER() — surface a
        # readable message rather than the raw PG diagnostic.
        msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        if "uq_contact_email_primary" in msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"email_primary={payload.email_primary!r} conflicts with an "
                    "existing active contact (case-insensitive)"
                ),
            ) from exc
        if "uq_contact_linkedin_url" in msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"linkedin_url={payload.linkedin_url!r} conflicts with an "
                    "existing active contact (case-insensitive)"
                ),
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc.orig or exc)) from exc

    await db.refresh(row)
    return _contact_detail_dict(row)


@app.get("/contacts/{contact_id}", tags=["public"])
async def get_contact(contact_id: uuid.UUID, db: DbSession) -> dict[str, Any]:
    """Full contact detail (PR #52).

    Separate from ``GET /contacts`` so the list payload stays lean —
    operator-only fields (``notes``, ``contact_opt_in_topics``,
    ``source_metadata``, …) load only when a row is opened. Mirrors
    the ``GET /postings`` vs ``GET /postings/{id}`` split.

    Includes archived contacts (no filter on ``archived_at``) — the
    operator can open an archived row to unarchive it or to view its
    outreach history.
    """
    from sqlalchemy import select

    from job_assist.db.models import Contact

    row = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id} not found")
    return _contact_detail_dict(row)


@app.patch("/contacts/{contact_id}", tags=["public"])
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    db: DbSession,
) -> dict[str, Any]:
    """Partial update of a contact's mutable fields (PR #52).

    Immutable fields (``id``, ``created_at``, ``source_type``,
    ``first_name``, ``last_name``) are rejected via the schema's
    ``extra='forbid'``. Operators who think a name is wrong should
    archive + recreate rather than rename.

    Reachability is re-asserted after applying the diff — if the
    operator clears both ``email_primary`` and ``linkedin_url``, the
    CHECK constraint would fire as a 500. Catch it ourselves and
    return 422 with a clean message.

    Linking to an unknown ``target_company_id`` returns 404.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from job_assist.db.models import Contact, TargetCompany

    diff = payload.model_dump(exclude_unset=True)

    row = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id} not found")

    if "target_company_id" in diff and diff["target_company_id"] is not None:
        exists = (
            await db.execute(
                select(TargetCompany.id).where(TargetCompany.id == diff["target_company_id"]),
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"target_company {diff['target_company_id']} not found",
            )

    # Pre-apply reachability check: would this diff leave the row with
    # neither email_primary nor linkedin_url? Easier to catch here than
    # to translate the PG CHECK violation back into a useful message.
    future_email = diff.get("email_primary", row.email_primary)
    future_linkedin = diff.get("linkedin_url", row.linkedin_url)
    if future_email is None and future_linkedin is None:
        raise HTTPException(
            status_code=422,
            detail="at least one of email_primary or linkedin_url must remain set",
        )

    for key, value in diff.items():
        setattr(row, key, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        if "uq_contact_email_primary" in msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"email_primary={diff.get('email_primary')!r} conflicts with an "
                    "existing active contact (case-insensitive)"
                ),
            ) from exc
        if "uq_contact_linkedin_url" in msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"linkedin_url={diff.get('linkedin_url')!r} conflicts with an "
                    "existing active contact (case-insensitive)"
                ),
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc.orig or exc)) from exc

    await db.refresh(row)
    return _contact_detail_dict(row)


@app.post("/contacts/{contact_id}/archive", tags=["public"], status_code=204)
async def archive_contact(contact_id: uuid.UUID, db: DbSession) -> None:
    """Soft-delete a contact (PR #52).

    Sets ``archived_at = now()``. Idempotent — archiving an already-
    archived contact is a no-op (returns 204). The partial UNIQUE
    indexes on email + LinkedIn are scoped to ``archived_at IS NULL``
    so archiving frees the dedup slot for future re-ingests.

    Outreach history is preserved — ``archived_at`` is not DELETE.
    """
    from sqlalchemy import select
    from sqlalchemy.sql import func as sa_func

    from job_assist.db.models import Contact

    row = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id} not found")

    if row.archived_at is None:
        # Use the DB's now() so the timestamp matches the index scope
        # semantics exactly. Avoid Python-side now() drift.
        row.archived_at = (await db.execute(select(sa_func.now()))).scalar_one()
        await db.commit()


@app.post("/contacts/{contact_id}/unarchive", tags=["public"], status_code=204)
async def unarchive_contact(contact_id: uuid.UUID, db: DbSession) -> None:
    """Reverse :func:`archive_contact` (PR #52).

    Clears ``archived_at``. Idempotent — unarchiving an active row
    is a no-op (returns 204).

    On conflict (a different active contact now occupies the email
    / LinkedIn unique slot the archived row used to hold), returns
    422 with a clean message rather than a 500.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from job_assist.db.models import Contact

    row = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id} not found")

    if row.archived_at is None:
        return  # already active

    row.archived_at = None
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        if "uq_contact_" in msg:
            raise HTTPException(
                status_code=422,
                detail=(
                    "cannot unarchive: another active contact already holds this "
                    "email_primary or linkedin_url (case-insensitive)"
                ),
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc.orig or exc)) from exc


@app.post("/contacts/{contact_id}/outreach", tags=["public"], status_code=201)
async def log_outreach(
    contact_id: uuid.UUID,
    payload: OutreachMessageCreate,
    db: DbSession,
) -> dict[str, Any]:
    """Log an operator-sent or received outreach message (PR #52).

    ``source`` is forced to ``'manual'`` server-side; this PR only
    writes manual rows. PR #53 will add a gmail_auto path via a
    separate internal code path that bypasses this schema.

    ``posting_id`` is validated with a pre-check + 404 (mirrors PR #31's
    posting_action precedent) so the operator gets a clean error
    instead of a 500-via-IntegrityError.

    Outreach against an archived contact is allowed — the operator
    may receive an inbound reply from someone they've stopped
    initiating with.
    """
    from sqlalchemy import literal, select

    from job_assist.db.models import Contact, JobPosting, OutreachMessage

    # Contact must exist (any archive state is fine).
    contact_exists = (
        await db.execute(
            select(literal(1)).where(Contact.id == contact_id).limit(1),
        )
    ).scalar_one_or_none()
    if contact_exists is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id} not found")

    if payload.posting_id is not None:
        posting_exists = (
            await db.execute(
                select(literal(1)).where(JobPosting.id == payload.posting_id).limit(1),
            )
        ).scalar_one_or_none()
        if posting_exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"job_posting {payload.posting_id} not found",
            )

    row = OutreachMessage(
        contact_id=contact_id,
        direction=payload.direction,
        channel=payload.channel,
        subject=payload.subject,
        body=payload.body,
        sent_at=payload.sent_at,
        posting_id=payload.posting_id,
        source="manual",  # forced server-side; PR #53 writes gmail_auto
        external_message_id=None,
        message_metadata=payload.message_metadata,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _outreach_message_dict(row)


@app.get("/contacts/{contact_id}/outreach", tags=["public"])
async def list_contact_outreach(
    contact_id: uuid.UUID,
    db: DbSession,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated outreach timeline for one contact (PR #52).

    Newest-first via ``ORDER BY sent_at DESC, id ASC``. Stable
    ``id ASC`` tiebreaker on same-timestamp rows (PR #53 imports
    may bulk-insert at the same instant).

    2-query budget: one COUNT, one SELECT.

    Returning an empty page for a non-existent contact_id would be
    indistinguishable from "contact exists but has no outreach yet";
    pre-check + 404 to disambiguate.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import literal, select

    from job_assist.db.models import Contact, OutreachMessage

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    contact_exists = (
        await db.execute(
            select(literal(1)).where(Contact.id == contact_id).limit(1),
        )
    ).scalar_one_or_none()
    if contact_exists is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id} not found")

    count_stmt = (
        select(sa_func.count())
        .select_from(OutreachMessage)
        .where(OutreachMessage.contact_id == contact_id)
    )
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    rows_stmt = (
        select(OutreachMessage)
        .where(OutreachMessage.contact_id == contact_id)
        .order_by(OutreachMessage.sent_at.desc(), OutreachMessage.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(rows_stmt)).scalars().all()
    items = [_outreach_message_dict(m) for m in rows]
    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/outreach/recent", tags=["public"])
async def list_outreach_recent(
    db: DbSession,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Cross-contact outreach feed (PR #52).

    Drives the upcoming follow-up cron (PR #54) — "who haven't I
    heard back from in N days?" — but useful to expose now as the
    Contacts-page-wide activity view.

    2-query budget: one COUNT, one SELECT-with-JOIN. The JOIN pulls
    minimal contact context (first_name, last_name, source_type) so
    the feed renders without a per-row contact lookup.

    Ordering: ``sent_at DESC, id ASC`` — same stable tiebreaker as
    the per-contact view.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from job_assist.db.models import Contact, OutreachMessage

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    count_stmt = select(sa_func.count()).select_from(OutreachMessage)
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    rows_stmt = (
        select(
            OutreachMessage,
            Contact.first_name.label("c_first_name"),
            Contact.last_name.label("c_last_name"),
            Contact.source_type.label("c_source_type"),
        )
        .join(Contact, Contact.id == OutreachMessage.contact_id)
        .order_by(OutreachMessage.sent_at.desc(), OutreachMessage.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(rows_stmt)).all()

    items: list[dict[str, Any]] = []
    for row in rows:
        m = row[0]
        items.append(
            {
                **_outreach_message_dict(m),
                "contact_first_name": row.c_first_name,
                "contact_last_name": row.c_last_name,
                "contact_source_type": row.c_source_type,
            }
        )
    return {"total": total, "offset": offset, "limit": limit, "items": items}


# ── Admin — Gmail backfill + poll ─────────────────────────────────────────────


def _missing_gmail_env() -> list[str]:
    """Names of required Gmail env vars that are currently unset.

    Shared by ``/admin/gmail/backfill`` and ``/admin/gmail/poll`` so both
    endpoints return the same 503 message when the operator hasn't set
    one of the three required Railway variables.
    """
    return [
        name
        for name, value in (
            ("GMAIL_CREDENTIALS_JSON", settings.gmail_credentials_json),
            ("GMAIL_REFRESH_TOKEN", settings.gmail_refresh_token),
            ("GEMINI_API_KEY", settings.gemini_api_key),
        )
        if not value
    ]


def _build_gmail_runtime() -> tuple[Any, Any]:
    """Construct GmailClient + EmailClassifier from settings.

    Lazy-imports the SDK-touching modules so test setups that monkeypatch
    ``google.genai`` don't have to fight import order.
    """
    from job_assist.gmail.classifier import EmailClassifier
    from job_assist.gmail.client import GmailClient

    gmail = GmailClient(
        credentials_json=settings.gmail_credentials_json,
        refresh_token=settings.gmail_refresh_token,
    )
    classifier = EmailClassifier(api_key=settings.gemini_api_key)
    return gmail, classifier


def _surface_gmail_failure(endpoint: str, exc: BaseException) -> HTTPException:
    """Convert an unhandled exception from a Gmail admin endpoint into a
    diagnostic 500 with a structured body.

    Why this exists: the gmail-poll GitHub Action only sees the HTTP
    response. A bare ``Internal Server Error`` body forces the operator
    to pull Railway logs to find out whether it was an OAuth token
    revocation, a Gemini quota error, or a DB connectivity hiccup.
    Surfacing ``exc_type`` + ``exc_message`` (one line, truncated) lets
    CI alerts and the workflow summary diagnose at a glance.

    Also logs the full traceback to Railway stdout (``exc_info=True``)
    so the deep stack is still available when needed.
    """
    logger = logging.getLogger("job_assist.main")
    logger.exception("Gmail endpoint %s failed", endpoint)
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    # Cap message length — some Google API errors include the full
    # request URL with a long auth header.
    if len(msg) > 500:
        msg = msg[:500] + "…(truncated)"
    return HTTPException(
        status_code=500,
        detail={
            "endpoint": endpoint,
            "exc_type": type(exc).__name__,
            "exc_message": msg,
            "hint": (
                "Common causes: GMAIL_REFRESH_TOKEN revoked (re-run OAuth flow), "
                "GEMINI_API_KEY rotated/exhausted, Railway DB unreachable. "
                "Full traceback in Railway logs."
            ),
        },
    )


async def _sync_applied_companies_best_effort(db: AsyncSession) -> None:
    """Reflect newly-crawled applications in the Companies list (tracking-only).

    Runs on the Gmail crawl tail. Best-effort: a failure here must NEVER fail
    the crawl (same contract as the embeddings recalibrate-on-sweep-tail). The
    sweep itself is idempotent, so the next crawl picks up anything missed.
    """
    try:
        from job_assist.services.applied_companies import sync_applied_companies

        await sync_applied_companies(db)
    except Exception as exc:
        await db.rollback()
        logging.getLogger("job_assist.main").warning(
            "applied_companies.sync_hook_failed", extra={"error": str(exc)[:300]}
        )


@app.post(
    "/admin/gmail/backfill",
    responses={409: {"description": "Another Gmail sweep (poll or backfill) is already running"}},
)
async def gmail_backfill(
    db: DbSession,
    days: int = 60,
) -> dict[str, Any]:
    """Pull the last ``days`` days of mail, classify each, write outcome_event rows.

    Long-running (~5-10 minutes for a 60-day window on the Gemini free tier
    because of the 15 RPM throttle). Idempotent: re-running over the same
    window skips messages whose ``email_message_id`` is already in the table.

    Returns 503 with a clear hint when any of the required env vars
    (``GMAIL_CREDENTIALS_JSON``, ``GMAIL_REFRESH_TOKEN``, ``GEMINI_API_KEY``)
    are missing — preferable to a 500 stack trace.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    missing = _missing_gmail_env()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Gmail backfill unavailable: missing env var(s) {missing}. "
                "Set these on Railway (or .env locally) and retry."
            ),
        )

    from job_assist.gmail.backfill import run_backfill
    from job_assist.gmail.sweep_lock import GmailSweepBusyError, gmail_sweep_slot
    from job_assist.services.gmail_sweep_run import record_sweep

    try:
        # fix(audit): one Gmail sweep at a time. A backfill overlapping the
        # 15-min cron poll double-spends Gemini on shared messages and then
        # IntegrityErrors on the unique email_message_id, aborting the batch.
        # feat/gmail-health-check: same sweep recording as the poll path.
        async with gmail_sweep_slot(), record_sweep("backfill") as sweep:
            gmail, classifier = _build_gmail_runtime()
            report = await run_backfill(db, gmail, classifier, days_back=days)
            sweep.set_counts(report)
    except GmailSweepBusyError:
        raise HTTPException(
            status_code=409,
            detail="A Gmail sweep (poll or backfill) is already running — retry when it finishes.",
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        raise _surface_gmail_failure("/admin/gmail/backfill", exc) from exc
    await _sync_applied_companies_best_effort(db)
    return report.model_dump(mode="json")


@app.post(
    "/admin/gmail/poll",
    responses={409: {"description": "Another Gmail sweep (poll or backfill) is already running"}},
)
async def gmail_poll(db: DbSession) -> dict[str, Any]:
    """Poll Gmail for messages received since the most recent outcome_event.

    Designed to be called every 15 minutes by the ``gmail-poll`` workflow.
    Idempotent at the message level (same ``email_message_id`` pre-check
    as the backfill). The watermark is derived from
    ``MAX(outcome_event.received_at)`` on every call — no separate state
    table to drift out of sync.

    Returns 503 with a clear hint when any required env var is missing
    (same contract as ``/admin/gmail/backfill``).

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    missing = _missing_gmail_env()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Gmail poll unavailable: missing env var(s) {missing}. "
                "Set these on Railway (or .env locally) and retry."
            ),
        )

    from job_assist.gmail.backfill import run_poll
    from job_assist.gmail.sweep_lock import GmailSweepBusyError, gmail_sweep_slot
    from job_assist.services.gmail_sweep_run import record_sweep

    try:
        # fix(audit): one Gmail sweep at a time — see /admin/gmail/backfill.
        # A 409 here is benign for the cron: the running sweep covers the
        # same window and the next 15-min tick retries.
        # feat/gmail-health-check: record the sweep (start/finish/runtime/
        # status) so the health monitor reports Gmail liveness + runtime.
        async with gmail_sweep_slot(), record_sweep("poll") as sweep:
            gmail, classifier = _build_gmail_runtime()
            report = await run_poll(db, gmail, classifier)
            sweep.set_counts(report)
    except GmailSweepBusyError:
        raise HTTPException(
            status_code=409,
            detail="A Gmail sweep (poll or backfill) is already running — the next poll retries.",
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        raise _surface_gmail_failure("/admin/gmail/poll", exc) from exc
    await _sync_applied_companies_best_effort(db)
    # feat/applied-pipeline-crosslink: link freshly-inserted outcomes to a
    # specific posting by role so the Pipeline/posting cross-links populate.
    # Best-effort — a linker failure must never fail the poll. Only new
    # outcomes (job_posting_id IS NULL) are scanned; idempotent.
    try:
        from job_assist.services.outcome_posting_match import link_outcomes_to_postings

        await link_outcomes_to_postings(db)
    except Exception as exc:
        await db.rollback()
        logging.getLogger("job_assist.main").warning(
            "outcome_posting_match.poll_tail_failed", extra={"error": str(exc)[:300]}
        )
    return report.model_dump(mode="json")


@app.post("/admin/outcomes/relink", tags=["admin"])
async def outcomes_relink(
    db: DbSession,
    use_classifier: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Re-link unlinked ``outcome_event`` rows to ``target_company``.

    Re-runs the matcher in ``gmail/backfill._match_target_company`` over
    rows where ``target_company_id IS NULL AND outcome_type IS
    job-related``. Used after either (a) the operator hand-fills
    ``target_company.domain`` via the seed endpoint with
    ``backfill_nullables=true``, or (b) the matcher itself is softened
    (this PR adds Team/Recruiting/Holdings suffix patterns + leading-
    article strip + relaxed unique-candidate check). Service-level docs
    in ``services/outcome_relink.py``.

    Query params:
      * ``use_classifier=true`` — re-derive ``extracted_company`` via
        Gemini on the persisted ``raw_snippet`` for rows the domain
        path doesn't catch. Slow: ~4s per row under the free-tier
        throttle (~12 min for ~177 production unlinked rows). When
        ``false`` (default), only the domain path runs — cheap, no LLM
        cost.
      * ``limit=N`` — cap on rows scanned. Useful for paginating a
        large backlog or smoke-testing with ``?limit=5`` first.

    Idempotent: rows with a non-NULL ``target_company_id`` are
    excluded by the WHERE clause, so re-runs never overwrite existing
    links and a partial mid-run crash resumes cleanly on the next
    invocation.

    Returns 503 when ``use_classifier=true`` and ``GEMINI_API_KEY`` is
    missing (same env-var guard ``/admin/gmail/poll`` uses).

    TODO: add authentication before exposing publicly. Currently
    dev-mode only — single-user deployment.
    """
    from job_assist.services.outcome_relink import relink_unmatched

    classifier = None
    if use_classifier:
        # Re-use the env-var preflight + builder from /admin/gmail/poll
        # so the failure mode is identical (503 with the missing-var
        # list, structured 500 on Gemini exceptions).
        missing = [name for name in _missing_gmail_env() if name == "GEMINI_API_KEY"]
        if missing:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Relink with use_classifier=true unavailable: missing env var(s) "
                    f"{missing}. Set on Railway and retry. To run domain-only "
                    "(no LLM), omit use_classifier or pass use_classifier=false."
                ),
            )
        # Only the classifier is needed — the GmailClient is wasted work
        # here because we already have the persisted email fields. But
        # the existing helper builds both; the GmailClient construction
        # is cheap and we discard it.
        _, classifier = _build_gmail_runtime()

    try:
        report = await relink_unmatched(
            db,
            classifier,
            use_classifier=use_classifier,
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _surface_gmail_failure("/admin/outcomes/relink", exc) from exc
    return report.model_dump(mode="json")


@app.post("/admin/outcomes/link-postings", tags=["admin"])
async def outcomes_link_postings(
    db: DbSession,
    limit: int | None = None,
) -> dict[str, Any]:
    """Link Gmail outcome_events to a SPECIFIC corpus posting by role (cross-link).

    Populates ``outcome_event.job_posting_id`` so the Pipeline (Gmail) and the
    triage/Applied posting can cross-reference each other. Cross-link ONLY —
    purely navigational; it does not change any status, feed scoring, or affect
    tab membership (the posting-specific Applied/Rejected fix is preserved).

    Matching (``services/outcome_posting_match``) is deterministic and
    conservative: candidates are OPEN postings at the email's already-resolved
    ``target_company_id``; the single best by role-token overlap is linked only
    when it clears a threshold AND (the company has one candidate OR it beats the
    runner-up by a margin). A company with many postings never fans out — an
    email maps to at-most-one posting, or stays Gmail-only.

    Idempotent (only ``job_posting_id IS NULL`` rows are considered). ``limit``
    caps rows scanned. No LLM cost.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from job_assist.services.outcome_posting_match import link_outcomes_to_postings

    report = await link_outcomes_to_postings(db, limit=limit)
    return report.model_dump(mode="json")


@app.post("/admin/companies/sync-applied", tags=["admin"])
async def sync_applied_companies_endpoint(
    db: DbSession,
    threshold: int = 2,
    limit: int | None = None,
) -> dict[str, Any]:
    """Reflect real application activity in the Companies list (TRACKING-ONLY).

    Scans ``application_confirmation`` outcomes, resolves a company name
    (existing link → subject extraction → skip; never ``from_domain``), and
    upserts: an existing company is annotated (its outcomes get linked, never
    duplicated); a net-new company seen ``>= threshold`` times becomes a
    ``source='applied'`` tracking row (``ats=unknown``, ``ats_handle=NULL``,
    ``tier=NULL``) — which the ingest plan never crawls. One-off names are
    returned in ``suggested`` WITHOUT committing.

    NO ATS resolution (operator decision): tracking rows can never be ingested.
    Read-mostly; the only writes are tracking rows + ``target_company_id`` links.
    """
    from job_assist.services.applied_companies import sync_applied_companies

    report = await sync_applied_companies(db, threshold=threshold, limit=limit)
    return report.model_dump(mode="json")


# ── Operator profile ──────────────────────────────────────────────────────────


@app.get("/operator/profile", tags=["operator"])
async def get_operator_profile(db: DbSession) -> dict[str, Any]:
    """Return the singleton operator profile (id=1).

    500 if the row is missing — that would mean the seeding migration
    didn't run, which is a deployment bug rather than a runtime case.
    """
    from sqlalchemy import select

    from job_assist.db.models import OperatorProfile
    from job_assist.schemas.operator_profile import OperatorProfileRead

    row = (
        await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=500,
            detail="operator_profile id=1 is missing — seeding migration did not run",
        )
    return OperatorProfileRead.model_validate(row).model_dump(mode="json")


@app.put("/operator/profile", tags=["operator"])
async def update_operator_profile(
    payload: OperatorProfileUpdate,
    db: DbSession,
) -> dict[str, Any]:
    """Partial update of the singleton operator profile (id=1).

    Only fields present in the request body are touched. Validators on
    ``OperatorProfileUpdate`` strip / dedupe list fields and reject
    negative thresholds before the SQL UPDATE fires.

    FastAPI does the body validation itself — a 422 with a clean JSON
    error array fires automatically when a field validator raises.
    """
    from sqlalchemy import select

    from job_assist.db.models import OperatorProfile
    from job_assist.schemas.operator_profile import OperatorProfileRead

    row = (
        await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=500,
            detail="operator_profile id=1 is missing — seeding migration did not run",
        )

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)

    await db.commit()
    await db.refresh(row)

    # Semantic profile embedding (slice 1): re-embed looking_for_text when it
    # changed (hash-gated inside the helper). Wrapped so an embedding failure
    # NEVER fails the profile save — same "must not cascade" contract as
    # scoring at ingest. This is the ONLY scoring-adjacent side effect, and it
    # writes only the operator_profile vector columns; fit_score is untouched.
    try:
        from job_assist.services.embeddings import (
            embed_profile_if_changed,
            recalibrate_similarity,
        )

        changed = await embed_profile_if_changed(db)
        # slice 2a: the profile vector drives every posting's cosine, so a
        # changed profile invalidates similarity_score — recompute the
        # calibration. Best-effort; never fails the save.
        if changed:
            await recalibrate_similarity(db)
            # slice 2b: the recalibrated similarity_score feeds the scorer's
            # semantic_fit feature — re-score open postings so a looking_for_text
            # edit actually moves fit_score (not just the next embedding sweep).
            from job_assist.services.rescore import rescore_open_postings

            await rescore_open_postings(db)
    except Exception as exc:
        logging.getLogger("job_assist.main").warning(
            "operator_profile.embed_failed", extra={"error": str(exc)[:300]}
        )

    return OperatorProfileRead.model_validate(row).model_dump(mode="json")


# ── Admin — reclassify sweep (PR #48) ────────────────────────────────────────


@app.post("/admin/reclassify/sweep", tags=["admin"])
async def reclassify_sweep_endpoint(
    payload: ReclassifySweepRequest,
    db: DbSession,
) -> ReclassifySweepResponse:
    """Reclassify up to ``limit`` postings using the Gemini LLM classifier.

    Replaces the ingest-time regex heuristic (``adapters/normalization.py``)
    for existing rows.  Idempotent — re-running with the same LLM response
    produces the same values; ``changed`` will be 0.

    Selection order: oldest ``classified_at`` first (NULLs first so
    never-classified rows are processed before already-classified ones).

    On per-row LLM failure: log + skip + continue.  The row's original
    ``role_family`` / ``seniority_level`` is preserved.

    ``distribution`` in the response is a full-table snapshot taken AFTER
    the sweep so the operator can see the cumulative effect.

    TODO: add authentication before exposing publicly.
          Currently dev-mode only — single-user deployment.
    """
    from datetime import UTC, datetime

    from sqlalchemy import func, or_, select, text

    from job_assist.db.models import JobPosting
    from job_assist.db.models.operator_profile import OperatorProfile
    from job_assist.db.models.target_company import TargetCompany
    from job_assist.schemas.reclassify import ReclassifyDistribution
    from job_assist.services.classifier import (
        CLASSIFIER_VERSION,
        build_profile_context,
        classify_posting,
    )
    from job_assist.services.scoring import SCORER_VERSION, score_posting

    # ── 1. Select candidates ──────────────────────────────────────────────
    # Skip stale/closed postings (Bestiary 5.18) — don't burn LLM calls
    # reclassifying postings removed from their ATS board.
    stmt = select(JobPosting).where(JobPosting.closed_at.is_(None))
    if payload.only_unclassified:
        stmt = stmt.where(
            or_(
                cast(JobPosting.role_family, Text) == "other",
                cast(JobPosting.seniority_level, Text) == "unknown",
            )
        )
        # fix(audit): skip rows THIS classifier version already judged — an
        # LLM-confirmed 'other'/'unknown' stayed in the bucket forever and was
        # re-sent to Gemini daily, producing the same answer at the same
        # CLASSIFIER_VERSION (pure wasted paid calls, up to `limit` per day,
        # contradicting the workflow's "sweeps regex-failures" intent). A
        # version BUMP keeps them re-keyable — only same-version re-buys are
        # blocked. The health check's reclassify_pending mirrors this clause.
        stmt = stmt.where(
            or_(
                JobPosting.classified_at.is_(None),
                JobPosting.classifier_version.is_(None),
                JobPosting.classifier_version != CLASSIFIER_VERSION,
            )
        )
    # Oldest classified_at first; NULLs sort first so never-LLM-classified
    # rows are processed before rows the sweep has already touched.
    #
    # FOR UPDATE SKIP LOCKED (feat/sweep-skip-locked): this sweep commits ONCE at
    # the end, so the locks taken here are held for the whole run — an overlapping
    # sweep (delayed cron + manual trigger) skips these rows and works a disjoint
    # set instead of double-calling Gemini on the same postings.
    stmt = (
        stmt.order_by(
            JobPosting.classified_at.asc().nulls_first(),
            JobPosting.first_seen_at.asc(),
        )
        .limit(payload.limit)
        .with_for_update(skip_locked=True)
    )

    rows = (await db.execute(stmt)).scalars().all()

    # PR #56: load the operator profile once for the post-classification
    # rescoring pass below. NULL profile means the table is unseeded —
    # skip rescoring rather than fail the classifier sweep.
    op_row = await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    operator_profile = op_row.scalar_one_or_none()

    # slice 2b: inject the operator's free-form targets + keywords into the
    # classifier as DISAMBIGUATION context (None when unseeded → prompt
    # unchanged). The LLM reclassifier is where the profile text matters; the
    # title-regex ingest pass stays a fast, profile-agnostic first pass.
    profile_context = (
        build_profile_context(
            operator_profile.looking_for_text,
            operator_profile.role_keywords,
        )
        if operator_profile is not None
        else None
    )

    processed = 0
    changed = 0
    skipped = 0

    for posting in rows:
        processed += 1
        old_family = str(posting.role_family)
        old_seniority = str(posting.seniority_level)

        try:
            new_family, new_seniority = await classify_posting(
                posting.jd_text or "",
                posting.normalized_title,
                profile_context=profile_context,
            )
        except Exception as exc:
            logger.warning(
                "reclassify_sweep.row_failed",
                extra={
                    "posting_id": str(posting.id),
                    "error": str(exc)[:300],
                },
            )
            skipped += 1
            continue

        posting.role_family = new_family  # type: ignore[assignment]
        posting.seniority_level = new_seniority  # type: ignore[assignment]
        posting.classifier_version = CLASSIFIER_VERSION
        posting.classified_at = datetime.now(tz=UTC)

        # PR #56: rescore after each successful classification. role_family
        # and seniority_level are 50% of the composite weight; a sweep that
        # changes them must update fit_score to match. Defensive try/except
        # mirrors the ingest path — a scoring bug must not cascade to fail
        # the whole sweep.
        if operator_profile is not None:
            try:
                tier_value: int | None = None
                if posting.target_company_id is not None:
                    tier_row = await db.execute(
                        select(TargetCompany.tier).where(
                            TargetCompany.id == posting.target_company_id
                        )
                    )
                    tier_value = tier_row.scalar_one_or_none()
                posting.fit_score = score_posting(
                    posting,
                    operator_profile,
                    tier=tier_value,
                )
                posting.scored_at = datetime.now(tz=UTC)
                posting.scorer_version = SCORER_VERSION
            except Exception as exc:
                logger.warning(
                    "reclassify_sweep.scoring_failed",
                    extra={
                        "posting_id": str(posting.id),
                        "error": str(exc)[:300],
                    },
                )

        if new_family != old_family or new_seniority != old_seniority:
            changed += 1

    if processed > skipped:
        await db.commit()

    # ── 2. Distribution snapshot (full table, two queries) ────────────────
    rf_rows = (
        await db.execute(
            select(
                func.lower(cast(JobPosting.role_family, Text)).label("val"),
                func.count().label("cnt"),
            ).group_by(text("val"))
        )
    ).all()
    sn_rows = (
        await db.execute(
            select(
                func.lower(cast(JobPosting.seniority_level, Text)).label("val"),
                func.count().label("cnt"),
            ).group_by(text("val"))
        )
    ).all()

    return ReclassifySweepResponse(
        processed=processed,
        changed=changed,
        skipped=skipped,
        distribution=ReclassifyDistribution(
            role_family={row.val: row.cnt for row in rf_rows},
            seniority={row.val: row.cnt for row in sn_rows},
        ),
    )


# ── Admin — score sweep (PR #56) ─────────────────────────────────────────────


@app.post("/admin/score/sweep", tags=["admin"])
async def score_sweep_endpoint(
    payload: ScoreSweepRequest,
    db: DbSession,
) -> ScoreSweepResponse:
    """Score up to ``limit`` postings using the heuristic fit-scoring model.

    Selection order (PR #56):
      * ``only_unscored=True`` (default) — postings with ``fit_score IS NULL``,
        ordered ``first_seen_at ASC, id ASC`` (stable tiebreaker per the
        bestiary — postings sharing a same-second first_seen_at land in a
        deterministic order across runs).
      * ``only_unscored=False`` — all postings, ordered
        ``scored_at NULLS FIRST, first_seen_at ASC, id ASC`` so previously
        scored rows get refreshed in oldest-first order.

    Batched-loop termination: stop when ``remaining == 0`` for
    ``only_unscored=True`` (the backlog has drained). For
    ``only_unscored=False`` every open posting is always re-selectable, so
    ``processed`` and ``remaining`` never fall below ``limit`` — stop on
    ``changed == 0`` (scores have converged) instead.

    On per-row scoring failure: log + skip + continue. The row's previous
    ``fit_score`` is preserved (the score is decoration; the sweep must
    never wipe data on a transient failure).

    ``distribution`` in the response is a coarse-bucket snapshot of the
    FULL table (not just the rows this sweep touched) taken AFTER the
    sweep, so the operator sees the cumulative effect.

    TODO: add authentication before exposing publicly. Dev-mode only today.
    """
    from datetime import UTC, datetime

    from sqlalchemy import case, func, select
    from sqlalchemy.orm import defer

    from job_assist.db.models import JobPosting
    from job_assist.db.models.operator_profile import OperatorProfile
    from job_assist.db.models.target_company import TargetCompany
    from job_assist.schemas.score import ScoreDistribution
    from job_assist.services.scoring import SCORER_VERSION, bucket_for_score, score_posting

    # ── 0. Load operator profile (one read per sweep) ────────────────────
    op_row = await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    operator_profile = op_row.scalar_one_or_none()
    if operator_profile is None:
        raise HTTPException(
            status_code=400,
            detail="operator_profile is unseeded; cannot score postings",
        )

    # ── 1. Select candidates ─────────────────────────────────────────────
    # Tier comes from target_company via OUTER JOIN — postings without a
    # matched company get NULL tier, which the scorer maps to 50 (neutral).
    #
    # Defer the heavy columns the scorer never reads (full JD text + 768-float
    # JD vector + JD summary). The scorer needs only small structured fields +
    # the ``similarity_score`` int; loading the JD text/vector for every
    # candidate row ballooned memory on a fully-embedded corpus and OOMed the
    # worker. Deferring them keeps the sweep light (everything else loads — no
    # N+1).
    stmt = (
        select(JobPosting, TargetCompany.tier)
        .outerjoin(TargetCompany, JobPosting.target_company_id == TargetCompany.id)
        # Skip stale/closed postings (Bestiary 5.18) — no point scoring a
        # removed posting that won't surface in Triage anyway.
        .where(JobPosting.closed_at.is_(None))
        .options(
            defer(JobPosting.jd_text),
            defer(JobPosting.jd_embedding),
            defer(JobPosting.jd_summary_markdown),
        )
    )
    if payload.only_unscored:
        stmt = stmt.where(JobPosting.fit_score.is_(None))
    # Stable id ASC tiebreaker on every key (bestiary entry).
    #
    # FOR UPDATE SKIP LOCKED (feat/sweep-skip-locked): single end-of-loop commit,
    # so the locks span the run and overlapping sweeps skip these rows. ``of=
    # JobPosting`` locks ONLY the posting rows — locking the nullable side of the
    # TargetCompany outer join is invalid in Postgres. (Scoring makes no Gemini
    # call; this just keeps the sweep a clean, non-double-working queue.)
    stmt = (
        stmt.order_by(
            JobPosting.scored_at.asc().nulls_first(),
            JobPosting.first_seen_at.asc(),
            JobPosting.id.asc(),
        )
        .limit(payload.limit)
        .with_for_update(skip_locked=True, of=JobPosting)
    )

    rows = (await db.execute(stmt)).all()

    processed = 0
    changed = 0
    skipped = 0

    for posting, tier in rows:
        processed += 1
        old_score = posting.fit_score

        try:
            new_score = score_posting(posting, operator_profile, tier=tier)
        except Exception as exc:
            logger.warning(
                "score_sweep.row_failed",
                extra={
                    "posting_id": str(posting.id),
                    "error": str(exc)[:300],
                },
            )
            skipped += 1
            continue

        posting.fit_score = new_score
        posting.scorer_version = SCORER_VERSION
        posting.scored_at = datetime.now(tz=UTC)

        if new_score != old_score:
            changed += 1

    if processed > skipped:
        await db.commit()

    # ── 2. Distribution snapshot (full table, single GROUP BY) ───────────
    # Coarse buckets keep the response payload small; bucket_for_score maps
    # the integer to the label inline via a CASE expression so we don't need
    # a Python loop over every row.
    bucket_label = case(
        (JobPosting.fit_score.is_(None), "unscored"),
        (JobPosting.fit_score >= 80, "80-100"),
        (JobPosting.fit_score >= 60, "60-79"),
        (JobPosting.fit_score >= 40, "40-59"),
        (JobPosting.fit_score >= 20, "20-39"),
        else_="0-19",
    ).label("bucket")
    dist_rows = (
        await db.execute(select(bucket_label, func.count().label("cnt")).group_by(bucket_label))
    ).all()
    _ = bucket_for_score  # imported for docstring referencing; CASE is the
    #                     # actual aggregator here.

    # ── 3. Convergence signal ────────────────────────────────────────────
    # ``remaining`` = open postings the next identical call would still
    # select, beyond what this batch covered. For only_unscored=True it's the
    # true leftover backlog (computed POST-commit, so the rows just scored are
    # already excluded) → loop until 0. For only_unscored=False every open row
    # is perpetually re-selectable, so this stays > 0 across stateless calls
    # and is NOT a stop signal — callers converge on ``changed == 0`` instead.
    # See ScoreSweepResponse.remaining. (We hit the non-terminating loop live:
    # a only_unscored=False caller looping on ``processed < limit`` ran 950+
    # idempotent batches before being killed.)
    remaining_stmt = (
        select(func.count()).select_from(JobPosting).where(JobPosting.closed_at.is_(None))
    )
    if payload.only_unscored:
        remaining_stmt = remaining_stmt.where(JobPosting.fit_score.is_(None))
        remaining = (await db.execute(remaining_stmt)).scalar_one()
    else:
        total_open = (await db.execute(remaining_stmt)).scalar_one()
        remaining = max(0, total_open - processed)

    return ScoreSweepResponse(
        processed=processed,
        changed=changed,
        skipped=skipped,
        remaining=remaining,
        distribution=ScoreDistribution(
            by_bucket={row.bucket: row.cnt for row in dist_rows},
        ),
    )


@app.post("/admin/score/backfill", tags=["admin"])
async def score_backfill_endpoint(db: DbSession, batch_size: int = 100) -> dict[str, Any]:
    """Re-score EVERY open posting with the current scorer, in ONE call.

    Unlike ``/admin/score/sweep`` (which re-scores up to ``limit`` rows and
    relies on the caller looping), this drains the whole corpus server-side via
    ``rescore_open_postings``, which paginates in fixed-memory ``batch_size``
    passes (heavy JD columns deferred, commit + expunge per batch). Peak memory
    is bounded regardless of corpus size — no OOM, no client-side retry storm.

    Use after a ``SCORER_VERSION`` bump to backfill the existing corpus. Returns
    the counts plus a coverage check: ``not_on_current_version`` is the number
    of open rows still NOT stamped with the live ``SCORER_VERSION`` — 0 means
    100% coverage.

    TODO: add authentication before exposing publicly. Dev-mode only today.
    """
    from sqlalchemy import func, select

    from job_assist.db.models import JobPosting
    from job_assist.services.rescore import rescore_open_postings
    from job_assist.services.scoring import SCORER_VERSION

    if batch_size < 1 or batch_size > 500:
        raise HTTPException(status_code=422, detail="batch_size must be 1..500")

    rescored, changed = await rescore_open_postings(db, batch_size=batch_size)

    total_open = (
        await db.execute(
            select(func.count()).select_from(JobPosting).where(JobPosting.closed_at.is_(None))
        )
    ).scalar_one()
    not_on_current = (
        await db.execute(
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.closed_at.is_(None))
            .where(JobPosting.scorer_version.is_distinct_from(SCORER_VERSION))
        )
    ).scalar_one()

    return {
        "rescored": rescored,
        "changed": changed,
        "total_open": total_open,
        "scorer_version": SCORER_VERSION,
        "not_on_current_version": not_on_current,
    }


# ── Admin — backfills ─────────────────────────────────────────────────────────


@app.post("/admin/backfill/department-team", tags=["admin"])
async def backfill_department_team_endpoint(db: DbSession) -> dict[str, int]:
    """Promote ``department`` / ``team`` from raw_payload to typed columns.

    Idempotent — only rows where both columns are NULL get touched. Safe
    to call repeatedly; the daily-ingest self-heal in IngestionService
    also fills these columns naturally on each re-ingest, so the one-shot
    backfill is mostly useful right after PR #28a's migration lands.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from job_assist.services.posting_backfill import backfill_department_team

    report = await backfill_department_team(db)
    return {
        "candidates": report.candidates,
        "updated": report.updated,
        "skipped_no_source": report.skipped_no_source,
        "skipped_no_data": report.skipped_no_data,
    }


# ── Public read endpoints (PR #30a) ───────────────────────────────────────────
#
# Pure SELECTs against the existing schema for the frontend's list and detail
# pages. No auth on these yet (matches the rest of the API today); same TODO
# about tightening before public exposure.


# fix/datacenter-egress-headers: derive from _INGESTABLE_ATS so the /postings
# (+ export) ats filter accepts every ATS a posting_source can actually carry.
# Previously hardcoded {greenhouse, lever, ashby}; it drifted when the Workday
# (#33) and iCIMS (#55) adapters shipped, so ?ats=workday / ?ats=icims 422'd —
# the SOURCE filter chips for those two were dead.
# feat/wellfound-ingest: decoupled from _INGESTABLE_ATS. The DAILY-PLAN set
# (_INGESTABLE_ATS) is the five free company-board adapters; Wellfound is
# query-driven via Apify and must NOT join that plan — but its postings DO
# carry posting_source.ats='wellfound', so the filter (+ export + frontend
# chip) must accept it.
_ALLOWED_ATS_VALUES = set(_INGESTABLE_ATS) | {"wellfound"}
_ALLOWED_REMOTE_TYPES = {"remote", "hybrid", "onsite"}
_ALLOWED_STATE_FILTER_VALUES = {
    "triage",
    "interested",
    "not_interested",
    "applied",
    "snoozed",
    # PR #50: ``rejected`` is the one ``state`` value that does NOT
    # derive from posting_action.action_type — it's an EXISTS check
    # against outcome_event. See the dual-table comment block in
    # ``list_postings`` where the predicate is built. Frontend pages
    # ``/passed`` and ``/rejected`` map to ``not_interested`` and
    # ``rejected`` respectively; that page-name → wire-value mapping
    # is documented on the page modules themselves.
    "rejected",
}
# PR #50: explicit IN list, not LIKE. New rejection outcome types added
# later (e.g. ``rejection_offer_declined``) should require an explicit
# conversation here rather than silently auto-matching. Same convention
# we apply to enum membership checks elsewhere.
_REJECTION_OUTCOME_TYPES = (
    "rejection_pre_screen",
    "rejection_post_screen",
    "rejection_post_interview",
)


def _enum_value(v: Any) -> str | None:
    """Coerce ``RemoteType.remote`` / plain ``"remote"`` / ``None`` to str|None.

    Freshly-built-but-unrefreshed ORM rows still hold the raw string the
    caller assigned; refreshed rows hold the StrEnum. The serialisation
    has to tolerate both.
    """
    if v is None:
        return None
    inner = getattr(v, "value", v)
    return str(inner) if inner is not None else None


def _validate_ats_filter(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    for v in values:
        if v not in _ALLOWED_ATS_VALUES:
            raise HTTPException(
                status_code=422,
                detail=f"ats={v!r} not in {sorted(_ALLOWED_ATS_VALUES)}",
            )
    return values


def _validate_remote_type_filter(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    for v in values:
        if v not in _ALLOWED_REMOTE_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"remote_type={v!r} not in {sorted(_ALLOWED_REMOTE_TYPES)}",
            )
    return values


def _validate_state_filter(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    for v in values:
        if v not in _ALLOWED_STATE_FILTER_VALUES:
            raise HTTPException(
                status_code=422,
                detail=f"state={v!r} not in {sorted(_ALLOWED_STATE_FILTER_VALUES)}",
            )
    return values


def _state_block(
    action_type: Any,
    reason: Any,
    snooze_until: Any,
    created_at: Any,
    resolved_status: Any = None,
    gmail_rejection: Any = None,
) -> dict[str, Any]:
    """Serialise the LATERAL state row (or NULLs) into a StateEmbedded dict.

    The first four columns are NULL together when no posting_action row
    exists for the posting. We surface that as ``current=None`` (still in
    triage) rather than omitting the field, so the frontend can rely on
    the key always being present.

    ``resolved_status`` (feat/manual-application-status) is the computed
    lifecycle status driving the Applied / Rejected tabs; ``gmail_rejection``
    is an informational flag (a company-level Gmail rejection exists) that
    the UI shows as a hint but never acts on.
    """
    return {
        "current": _enum_value(action_type),
        "reason": _enum_value(reason),
        "snooze_until": snooze_until.isoformat() if snooze_until else None,
        "current_at": created_at.isoformat() if created_at else None,
        "resolved_status": resolved_status if resolved_status else None,
        "gmail_rejection_hint": bool(gmail_rejection),
    }


def _extract_location_strings(locations_normalized: Any) -> list[str]:
    """Flatten the JSONB locations_normalized field into a list of city strings."""
    if not isinstance(locations_normalized, list):
        return []
    out: list[str] = []
    for entry in locations_normalized:
        if isinstance(entry, dict):
            for key in ("city", "region", "country", "raw"):
                val = entry.get(key)
                if isinstance(val, str) and val:
                    out.append(val)
                    break
    return out


@app.get("/postings", tags=["public"])
async def list_postings(
    db: DbSession,
    tier: Annotated[list[int] | None, Query()] = None,
    ats: Annotated[list[str] | None, Query()] = None,
    remote_type: Annotated[list[str] | None, Query()] = None,
    role_family: Annotated[list[str] | None, Query()] = None,
    state: Annotated[list[str] | None, Query()] = None,
    include_snoozed_past_only: bool = False,
    target_company_id: uuid.UUID | None = None,
    sort: SortKey = DEFAULT_SORT,
    per_company_cap: int | None = None,
    limit: int = 20,
    offset: int = 0,
    include_closed: bool = False,
    include_filtered: bool = False,
) -> dict[str, Any]:
    """Paginated list of postings with the company/role/source/state nested.

    Default sort: ``newest`` → ``first_seen_at DESC``. See
    ``schemas/public.py::SortKey`` for the full enum and column mapping.
    Every sort key gets ``job_posting.id ASC`` as a tiebreaker so
    pagination stays stable across same-second timestamps or NULL
    salary / tier rows. Two queries total:
      1. COUNT(*) over the same WHERE (joined onto the state LATERAL too,
         so state filters narrow the total the same way they narrow rows).
      2. SELECT with TWO LATERALs — most-recent posting_source and
         most-recent posting_action — folded into the main page query.

    State filter values: ``triage`` (no action OR latest = reset),
    ``interested``, ``not_interested``, ``applied``, ``snoozed``. Repeating
    ``?state=...&state=...`` ORs them. ``include_snoozed_past_only=true``
    further restricts the snoozed bucket to past-due / open >7d entries.

    TODO: add authentication before exposing publicly.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased

    from job_assist.db.models import JobPosting, PostingAction, PostingSource, TargetCompany
    from job_assist.services.postings_query import (
        PostingsViewSpec,
        build_view_parts,
        gmail_rejection_exists,
        resolved_status_expr,
    )
    from job_assist.services.scoring import display_tier

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    if per_company_cap is not None and per_company_cap < 0:
        raise HTTPException(
            status_code=422,
            detail="per_company_cap must be >= 0 (0 disables the cap entirely)",
        )
    # feat/tunable-per-company-cap: pass through as-is. None flows to the query
    # builder, which resolves the operator's persisted
    # operator_profile.per_company_cap inline (no extra round-trip); an explicit
    # ?per_company_cap= override (including 0 = disabled) wins.
    ats = _validate_ats_filter(ats)
    remote_type = _validate_remote_type_filter(remote_type)
    state = _validate_state_filter(state)
    # include_snoozed_past_only is only meaningful with state=snoozed in
    # the filter set — silently ignore it otherwise rather than 422'ing,
    # since the UI may flip the checkbox before the user picks snoozed.
    _ = PostingAction  # imported for the lateral builder; mypy-only ref

    # feat/triage-export-xlsx: WHERE / state-lateral / cap-CTE / ORDER BY
    # all come from the shared helper so ``GET /postings`` and
    # ``GET /postings/export.xlsx`` produce the SAME slice for the same
    # URL. See ``services/postings_query.py`` for the construction rules.
    spec = PostingsViewSpec.from_validated(
        tier=tier,
        ats=ats,
        remote_type=remote_type,
        role_family=role_family,
        state=state,
        include_snoozed_past_only=include_snoozed_past_only,
        target_company_id=target_company_id,
        sort=sort,
        per_company_cap=per_company_cap,
        include_closed=include_closed,
        include_filtered=include_filtered,
    )
    parts = build_view_parts(spec)
    base_join = parts.base_join
    where_clauses = parts.where_clauses
    recent_pa = parts.recent_pa
    capped_ids = parts.capped_ids
    order_clauses = parts.order_clauses

    # COUNT query — joins the state LATERAL only when a state filter is
    # active. Skipping the join in the no-filter case keeps the COUNT
    # plan trivial.
    count_select = select(func.count()).select_from(base_join)
    if parts.needs_state_lateral:
        count_select = count_select.select_from(base_join.outerjoin(recent_pa, true()))
    for clause in where_clauses:
        count_select = count_select.where(clause)
    if capped_ids is not None:
        # The cap reduces the visible row count — pagination math must
        # reflect what the operator actually sees. Otherwise "showing 27
        # of 142" lies because only 27 are reachable.
        count_select = count_select.where(JobPosting.id.in_(capped_ids))
    total: int = (await db.execute(count_select)).scalar_one() or 0

    # LATERAL subquery picks the most-recent posting_source per posting in
    # the same execute call as the main SELECT.
    ps_alias = aliased(PostingSource)
    recent_ps = (
        select(ps_alias.ats.label("ps_ats"), ps_alias.source_url.label("ps_url"))
        .where(ps_alias.job_posting_id == JobPosting.id)
        .order_by(ps_alias.fetched_at.desc())
        .limit(1)
        .lateral("recent_ps")
    )

    rows_stmt = (
        select(
            JobPosting,
            TargetCompany,
            recent_ps.c.ps_ats,
            recent_ps.c.ps_url,
            recent_pa.c.pa_action_type,
            recent_pa.c.pa_reason,
            recent_pa.c.pa_snooze_until,
            recent_pa.c.pa_created_at,
            # feat/manual-application-status: resolved lifecycle status +
            # informational Gmail-rejection flag for the row's StateEmbedded.
            resolved_status_expr(recent_pa).label("resolved_status"),
            gmail_rejection_exists().label("gmail_rejection"),
        )
        .select_from(base_join)
        .outerjoin(recent_ps, true())
        .outerjoin(recent_pa, true())
        .order_by(*order_clauses)
        .limit(limit)
        .offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)
    if capped_ids is not None:
        rows_stmt = rows_stmt.where(JobPosting.id.in_(capped_ids))

    rows = (await db.execute(rows_stmt)).all()

    items: list[dict[str, Any]] = []
    for (
        jp,
        tc,
        ps_ats,
        ps_url,
        pa_action_type,
        pa_reason,
        pa_snooze_until,
        pa_created_at,
        resolved_status,
        gmail_rejection,
    ) in rows:
        salary_block: dict[str, Any] | None = None
        if any(x is not None for x in (jp.salary_min, jp.salary_max, jp.salary_currency)):
            salary_block = {
                "min": jp.salary_min,
                "max": jp.salary_max,
                "currency": jp.salary_currency,
                "period": _enum_value(jp.salary_period),
            }

        items.append(
            {
                "id": str(jp.id),
                "company": {
                    "id": str(tc.id) if tc is not None else None,
                    "name": tc.name if tc is not None else jp.canonical_company_name,
                    "domain": tc.domain if tc is not None else None,
                    "description": tc.description if tc is not None else None,
                    # Slice 3: display tier coalesces company pedigree
                    # tier with the score-derived band for broad shells
                    # (tier NULL). Display-only — scoring is unchanged.
                    "tier": display_tier(tc.tier if tc is not None else None, jp.fit_score),
                },
                "role": {
                    "title": jp.normalized_title,
                    "family": _enum_value(jp.role_family),
                    "department": jp.department,
                    "team": jp.team,
                    "seniority": _enum_value(jp.seniority_level),
                },
                "location_raw": jp.location_raw,
                "locations_normalized": _extract_location_strings(jp.locations_normalized),
                "remote_type": _enum_value(jp.remote_type),
                "salary": salary_block,
                "source": {
                    "ats": str(ps_ats) if ps_ats else "unknown",
                    "url": ps_url,
                },
                "first_seen_at": jp.first_seen_at.isoformat() if jp.first_seen_at else None,
                # PR #57: wired to ``fit_score`` (PR #56's heuristic 0-100).
                # NULL on rows the score sweep hasn't visited yet.
                "score": jp.fit_score,
                # Slice 2b: calibrated 0-100 semantic similarity (NULL until the
                # corpus is recalibrated). Surfaced so the UI can show it
                # alongside fit_score for the best_fit_semantic sort.
                "similarity_score": jp.similarity_score,
                "state": _state_block(
                    pa_action_type,
                    pa_reason,
                    pa_snooze_until,
                    pa_created_at,
                    resolved_status,
                    gmail_rejection,
                ),
            }
        )

    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/postings/export.xlsx", tags=["public"])
async def export_postings_xlsx(
    db: DbSession,
    tier: Annotated[list[int] | None, Query()] = None,
    ats: Annotated[list[str] | None, Query()] = None,
    remote_type: Annotated[list[str] | None, Query()] = None,
    role_family: Annotated[list[str] | None, Query()] = None,
    state: Annotated[list[str] | None, Query()] = None,
    include_snoozed_past_only: bool = False,
    target_company_id: uuid.UUID | None = None,
    sort: SortKey = DEFAULT_SORT,
    per_company_cap: int | None = None,
    include_closed: bool = False,
    include_filtered: bool = False,
) -> Response:
    """Export the CURRENT FILTERED VIEW as a two-sheet xlsx — every row the
    Triage list would render for this URL, in the same sort order, with NO
    row cap (just pagination removed).

    Same filter / sort / cap vocabulary as ``GET /postings``, built from the
    SAME ``build_view_parts(spec)`` so the exported set is provably identical
    to the list's — minus ``limit``/``offset``. Output:
      * Sheet 1 ``Export Context`` — timestamp, corpus size, active
        filters, matched-before-cap count, score range, scorer weights,
        operator hard rules, plain-language notes on the score.
      * Sheet 2 ``Jobs`` — ALL matching rows by the operator-selected sort,
        with rank, company, role, fit_score and its five sub-scores, salary,
        location, remote_type, tier, ats_source, apply_url, first_seen,
        jd_summary_markdown.

    Cap semantics: the per-company cap (a VIEW filter the list also applies)
    is honored exactly as the visible view does, so "what I see filtered" ==
    "what I get". Only the export's old 40-row pagination cap is gone. The
    "matched-before-cap" count on Sheet 1 reports how many would have
    surfaced without the per-company cap so the reviewer can sense the funnel.
    Zero matches → a valid workbook with headers only (not an error).
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased

    from job_assist.db.models import JobPosting, OperatorProfile, PostingSource, TargetCompany
    from job_assist.services.postings_export import build_workbook_bytes
    from job_assist.services.postings_query import PostingsViewSpec, build_view_parts

    # Reuse the same validators the list endpoint uses; they raise 422
    # on bad input. per_company_cap is validated inline here (limit/
    # offset are not user-facing on this endpoint).
    if per_company_cap is not None and per_company_cap < 0:
        raise HTTPException(
            status_code=422,
            detail="per_company_cap must be >= 0 (0 disables the cap entirely)",
        )
    # feat/tunable-per-company-cap: pass through (None → builder resolves the
    # operator's persisted cap inline), so the export surfaces the SAME slice
    # the operator sees.
    ats = _validate_ats_filter(ats)
    remote_type = _validate_remote_type_filter(remote_type)
    state = _validate_state_filter(state)

    spec = PostingsViewSpec.from_validated(
        tier=tier,
        ats=ats,
        remote_type=remote_type,
        role_family=role_family,
        state=state,
        include_snoozed_past_only=include_snoozed_past_only,
        target_company_id=target_company_id,
        sort=sort,
        per_company_cap=per_company_cap,
        include_closed=include_closed,
        include_filtered=include_filtered,
    )
    parts = build_view_parts(spec)
    base_join = parts.base_join
    where_clauses = parts.where_clauses
    recent_pa = parts.recent_pa
    capped_ids = parts.capped_ids
    order_clauses = parts.order_clauses

    # Corpus size: total rows in job_posting (no filters). Cheap COUNT,
    # gives the reviewer "we exported N of M" framing on Sheet 1.
    corpus_size: int = (
        await db.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one() or 0

    # Matched-before-cap: filters applied, cap NOT applied. Reveals the
    # funnel the cap creates ("filters matched 142, cap surfaces 40").
    pre_cap_select = select(func.count()).select_from(base_join)
    if parts.needs_state_lateral:
        pre_cap_select = pre_cap_select.select_from(base_join.outerjoin(recent_pa, true()))
    for clause in where_clauses:
        pre_cap_select = pre_cap_select.where(clause)
    matched_before_cap: int = (await db.execute(pre_cap_select)).scalar_one() or 0

    # Operator profile (singleton id=1) — needed for score_breakdown and
    # the hard-rule context dump on Sheet 1.
    profile = (
        await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=500,
            detail="operator_profile id=1 missing — migration not seeded",
        )

    # Top-N rows in the visible sort. apply_url comes from the most-
    # recent posting_source lateral (same pattern list_postings uses for
    # ps_ats / ps_url).
    ps_alias = aliased(PostingSource)
    recent_ps = (
        select(
            ps_alias.ats.label("ps_ats"),
            ps_alias.apply_url.label("ps_apply_url"),
        )
        .where(ps_alias.job_posting_id == JobPosting.id)
        .order_by(ps_alias.fetched_at.desc())
        .limit(1)
        .lateral("recent_ps")
    )
    # NO ``.limit()`` — the export is the FULL filtered+sorted set, unbounded.
    # This is the only line that differs from list_postings' row query (which
    # adds ``.limit(limit).offset(offset)`` for pagination). Same base_join,
    # same WHERE, same ORDER BY, same per-company cap — so the export is
    # provably "the list without pagination".
    rows_stmt = (
        select(
            JobPosting,
            TargetCompany,
            recent_ps.c.ps_ats,
            recent_ps.c.ps_apply_url,
        )
        .select_from(base_join)
        .outerjoin(recent_ps, true())
        .order_by(*order_clauses)
    )
    # The state-filter WHERE clauses (built by build_view_parts) reference the
    # recent_pa LATERAL. list_postings OUTER-joins it onto its row query so an
    # un-actioned posting survives with pa_action_type=NULL — exactly what the
    # ``triage`` predicate (``pa_action_type IS NULL OR = 'reset'``) selects.
    # Without this join, referencing recent_pa columns folds it in as an
    # IMPLICIT INNER lateral, which drops every un-actioned posting before the
    # predicate runs — so a ``state=triage`` export returned 0 rows even though
    # the matched-before-cap count (which DOES outer-join) reported hundreds.
    # Gate on needs_state_lateral so the no-state export keeps its trivial plan.
    if parts.needs_state_lateral:
        rows_stmt = rows_stmt.outerjoin(recent_pa, true())
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)
    if capped_ids is not None:
        rows_stmt = rows_stmt.where(JobPosting.id.in_(capped_ids))

    rows = [tuple(r) for r in (await db.execute(rows_stmt)).all()]

    xlsx_bytes = build_workbook_bytes(
        spec=spec,
        profile=profile,
        rows=rows,
        corpus_size=corpus_size,
        matched_before_cap=matched_before_cap,
    )
    filename = f"triage-export-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/postings/{posting_id}", tags=["public"])
async def get_posting(
    posting_id: uuid.UUID,
    db: DbSession,
) -> dict[str, Any]:
    """Full detail for one posting, including matched division if any.

    Division match uses ``IS NOT DISTINCT FROM`` so a posting and a
    division with ``team IS NULL`` both join correctly — matches the
    semantics of the ``UNIQUE NULLS NOT DISTINCT`` constraint that
    populates the table in PR #28b.

    404 when the posting id doesn't exist.

    TODO: add authentication before exposing publicly.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from job_assist.db.models import (
        Division,
        JobPosting,
        OutcomeEvent,
        PostingAction,
        PostingSource,
        TargetCompany,
    )
    from job_assist.services.posting_actions import latest_action_lateral
    from job_assist.services.postings_query import gmail_rejection_exists, resolved_status_expr
    from job_assist.services.scoring import display_tier

    ps_alias = aliased(PostingSource)
    recent_ps = (
        select(ps_alias.ats.label("ps_ats"), ps_alias.source_url.label("ps_url"))
        .where(ps_alias.job_posting_id == JobPosting.id)
        .order_by(ps_alias.fetched_at.desc())
        .limit(1)
        .lateral("recent_ps")
    )
    recent_pa = latest_action_lateral()

    stmt = (
        select(
            JobPosting,
            TargetCompany,
            Division,
            recent_ps.c.ps_ats,
            recent_ps.c.ps_url,
            recent_pa.c.pa_action_type,
            recent_pa.c.pa_reason,
            recent_pa.c.pa_snooze_until,
            recent_pa.c.pa_created_at,
            resolved_status_expr(recent_pa).label("resolved_status"),
            gmail_rejection_exists().label("gmail_rejection"),
        )
        .select_from(JobPosting.__table__)
        .outerjoin(
            TargetCompany.__table__,
            JobPosting.target_company_id == TargetCompany.id,
        )
        .outerjoin(
            Division.__table__,
            and_(
                Division.target_company_id == JobPosting.target_company_id,
                Division.department.is_not_distinct_from(JobPosting.department),
                Division.team.is_not_distinct_from(JobPosting.team),
            ),
        )
        .outerjoin(recent_ps, true())
        .outerjoin(recent_pa, true())
        .where(JobPosting.id == posting_id)
    )

    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} not found")

    (
        jp,
        tc,
        div,
        ps_ats,
        ps_url,
        pa_action_type,
        pa_reason,
        pa_snooze_until,
        pa_created_at,
        resolved_status,
        gmail_rejection,
    ) = row

    # Full append-only audit trail, chronological ASC. Separate query so
    # the join cardinality on the detail SELECT stays one row.
    history_rows = (
        (
            await db.execute(
                select(PostingAction)
                .where(PostingAction.job_posting_id == posting_id)
                .order_by(PostingAction.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    state_history = [
        {
            "id": str(pa.id),
            "action_type": _enum_value(pa.action_type),
            "reason": _enum_value(pa.reason),
            "snooze_until": pa.snooze_until.isoformat() if pa.snooze_until else None,
            "notes": pa.notes,
            "created_at": pa.created_at.isoformat(),
        }
        for pa in history_rows
    ]

    salary_block: dict[str, Any] | None = None
    if any(x is not None for x in (jp.salary_min, jp.salary_max, jp.salary_currency)):
        salary_block = {
            "min": jp.salary_min,
            "max": jp.salary_max,
            "currency": jp.salary_currency,
            "period": _enum_value(jp.salary_period),
        }

    division_block: dict[str, Any] | None = None
    if div is not None:
        division_block = {
            "id": str(div.id),
            "department": div.department,
            "team": div.team,
            "description": div.description,
        }

    # feat/application-resume: the resume attached to THIS application (if any).
    # Metadata only — the file bytes stream from GET /postings/{id}/resume.
    from job_assist.db.models import ApplicationResume

    resume_row = (
        await db.execute(
            select(ApplicationResume).where(ApplicationResume.job_posting_id == posting_id)
        )
    ).scalar_one_or_none()
    resume_block = _resume_meta(resume_row) if resume_row is not None else None

    # feat/applied-pipeline-crosslink: the most-recent Gmail Pipeline outcome
    # LINKED to THIS specific posting (outcome_event.job_posting_id == id) — a
    # read-only pointer so the operator can jump to the Pipeline card. NULL when
    # no email matched this role. Posting-specific by construction (the matcher
    # links one email to at-most-one posting), never company-level — preserves
    # the no-fanout fix. Informational only: it does NOT affect state/scoring.
    gmail_outcome_row = (
        await db.execute(
            select(OutcomeEvent)
            .where(OutcomeEvent.job_posting_id == posting_id)
            .order_by(OutcomeEvent.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    gmail_outcome_block: dict[str, Any] | None = None
    if gmail_outcome_row is not None:
        gmail_outcome_block = {
            "outcome_event_id": str(gmail_outcome_row.id),
            "stage": _enum_value(gmail_outcome_row.outcome_type),
            "received_at": gmail_outcome_row.received_at.isoformat(),
            "email_thread_id": gmail_outcome_row.email_thread_id,
            "subject": gmail_outcome_row.subject,
        }

    return {
        "id": str(jp.id),
        "company": {
            "id": str(tc.id) if tc is not None else None,
            "name": tc.name if tc is not None else jp.canonical_company_name,
            "domain": tc.domain if tc is not None else None,
            "description": tc.description if tc is not None else None,
            # Slice 3: display tier coalesces pedigree tier with the
            # score-derived band for broad shells. Display-only.
            "tier": display_tier(tc.tier if tc is not None else None, jp.fit_score),
        },
        "role": {
            "title": jp.normalized_title,
            "family": _enum_value(jp.role_family),
            "department": jp.department,
            "team": jp.team,
            "seniority": _enum_value(jp.seniority_level),
        },
        "location_raw": jp.location_raw,
        "locations_normalized": _extract_location_strings(jp.locations_normalized),
        "remote_type": _enum_value(jp.remote_type),
        "salary": salary_block,
        "source": {
            "ats": str(ps_ats) if ps_ats else "unknown",
            "url": ps_url,
        },
        "first_seen_at": jp.first_seen_at.isoformat() if jp.first_seen_at else None,
        # PR #57: wired to ``fit_score`` (see PostingListItem schema comment).
        "score": jp.fit_score,
        # Slice 2b: calibrated semantic similarity (NULL until recalibrated).
        "similarity_score": jp.similarity_score,
        "state": _state_block(
            pa_action_type,
            pa_reason,
            pa_snooze_until,
            pa_created_at,
            resolved_status,
            gmail_rejection,
        ),
        "description_markdown": jp.jd_text or None,
        "jd_summary_markdown": jp.jd_summary_markdown,
        "division": division_block,
        "posted_at": jp.posted_at.isoformat() if jp.posted_at else None,
        "last_seen_at": jp.last_seen_at.isoformat() if jp.last_seen_at else None,
        "closed_at": jp.closed_at.isoformat() if jp.closed_at else None,
        "state_history": state_history,
        # feat/application-resume: per-application resume metadata (null if none).
        "resume": resume_block,
        # feat/applied-pipeline-crosslink: read-only pointer to the matched Gmail
        # Pipeline outcome (null if none). Informational; never moves state.
        "gmail_outcome": gmail_outcome_block,
    }


@app.post("/postings/{posting_id}/state", tags=["public"])
async def post_posting_state(
    posting_id: uuid.UUID,
    payload: PostingStateRequest,
    db: DbSession,
) -> dict[str, Any]:
    """Record one operator action against a posting (PR #31).

    Returns the resulting :class:`StateEmbedded` so the frontend can
    update its row without re-fetching the list.

    Error mapping:
      - Unknown ``action_type`` / ``reason``  → 422 (Pydantic enum coercion)
      - Cross-field rule violation             → 422 (ValueError from service)
      - Unknown ``posting_id``                 → 404 (LookupError from service)

    Lives under ``tags=["public"]`` because it's the same trust model as
    the rest of the public surface — single-user dev mode, TODO to lock
    down before any wider deployment.
    """
    from job_assist.services.posting_actions import application_resume_exists, record_action

    try:
        row = await record_action(
            db,
            posting_id,
            payload.action_type,
            payload.reason,
            payload.snooze_until,
            payload.notes,
            payload.resume_version_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # feat/triple-aware-apply (1b): warn-but-allow resume signal. ONLY on an
    # applied action do we report whether this posting has an application_resume
    # (the corpus link is the shared job_posting_id, not resume_version_id). The
    # apply already succeeded above — this is a read-only flag the UI warns on;
    # it never blocks the apply and never writes. null for non-applied actions
    # so the key is always present (matches the _state_block convention).
    resume_attached: bool | None = None
    if payload.action_type == ActionType.applied:
        resume_attached = await application_resume_exists(db, posting_id)

    block = _state_block(row.action_type, row.reason, row.snooze_until, row.created_at)
    block["resume_attached"] = resume_attached
    return block


# ── Bulk triage actions (feat/bulk-triage-actions) ───────────────────────────
# The default triage queue floods with non-PM noise (T3 broad-ingest), and
# per-posting passing is the only clear-out today. This applies ONE action to
# many postings in a single transaction. Bulk "Pass" (not_interested) is the
# main use; bulk "Reset" makes it reversible (reset is an append-only,
# no-side-effect action). Same record-action validation as the per-posting
# endpoint, run once for the shared (action_type, reason) tuple.

# Backstop so a runaway/over-eager client can't enqueue an unbounded write.
_BULK_STATE_MAX_IDS = 500


@app.post("/postings/bulk-state", tags=["public"])
async def post_bulk_posting_state(
    payload: BulkPostingStateRequest,
    db: DbSession,
) -> dict[str, Any]:
    """Record one action against many postings in a single transaction.

    The cross-field rules (reason required iff not_interested; reason null
    otherwise; snooze_until only with snoozed) are identical for every id, so
    they're validated ONCE up-front — a bulk Pass without a reason 422s before
    any write. Unknown / duplicate ids don't abort the batch: the existing ones
    are written and the misses are reported per-id.

    Body: ``{ids, action_type, reason?, snooze_until?, notes?}``.
    Returns: ``{succeeded, failed, failures: [{posting_id, error}]}``.

    Bulk-undo is the same endpoint with ``action_type='reset'`` (no reason).
    """
    from sqlalchemy import select

    from job_assist.db.models import JobPosting, PostingAction
    from job_assist.services.posting_actions import _validate

    if not payload.ids:
        raise HTTPException(status_code=422, detail="ids must not be empty")
    if len(payload.ids) > _BULK_STATE_MAX_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"too many ids (max {_BULK_STATE_MAX_IDS} per bulk action)",
        )

    # Validate the shared action/reason/snooze rule set once (same for all ids).
    try:
        _validate(payload.action_type, payload.reason, payload.snooze_until)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # One existence query for the whole batch (clean per-id 404 reporting
    # instead of an opaque FK IntegrityError aborting the transaction).
    existing = set(
        (await db.execute(select(JobPosting.id).where(JobPosting.id.in_(payload.ids))))
        .scalars()
        .all()
    )

    reason_value = payload.reason.value if payload.reason else None
    action_value = payload.action_type.value
    succeeded = 0
    failures: list[dict[str, str]] = []
    seen: set[uuid.UUID] = set()
    for pid in payload.ids:
        if pid in seen:
            continue  # idempotent within a batch — dedupe repeated ids
        seen.add(pid)
        if pid not in existing:
            failures.append({"posting_id": str(pid), "error": "job_posting not found"})
            continue
        db.add(
            PostingAction(
                job_posting_id=pid,
                action_type=action_value,
                reason=reason_value,
                snooze_until=payload.snooze_until,
                notes=payload.notes,
            )
        )
        succeeded += 1

    # Single commit: the valid rows land atomically.
    await db.commit()

    return {"succeeded": succeeded, "failed": len(failures), "failures": failures}


# ── Manual application status (feat/manual-application-status Phase 1) ────────
# Revives the dormant application_state table: one row per posting holding the
# operator's manual lifecycle stage (applied → interview → offer →
# accepted/rejected). UPSERT by job_posting_id, mirroring the resume upsert.
# Drives the Applied / Rejected tabs via resolved_status (postings_query.py):
# marking accepted/rejected drops the card out of Applied; rejected lands it in
# Rejected. Authoritative over the Gmail signal (which stays an informational
# hint). Phase 2 (the 14-day applied badge) will read applied_at.


@app.put("/postings/{posting_id}/status", tags=["public"])
async def put_application_status(
    posting_id: uuid.UUID,
    payload: ApplicationStatusUpdate,
    db: DbSession,
) -> dict[str, Any]:
    """Set the operator's manual lifecycle status for a posting.

    UPSERT on ``application_state`` by ``job_posting_id`` (SELECT-or-create,
    same shape as the resume upsert). ``applied_at`` is stamped the FIRST time
    any status is recorded — every lifecycle stage implies an application was
    submitted — and never overwritten thereafter, so it anchors Phase 2's
    14-day badge. ``updated_at`` auto-bumps via the column's ``onupdate``.

    Error mapping:
      - status outside the five lifecycle stages → 422 (Pydantic; DB CHECK is
        the backstop)
      - unknown ``posting_id``                   → 404
    """
    from sqlalchemy import select
    from sqlalchemy.sql import func as sa_func

    from job_assist.db.models import ApplicationState, JobPosting

    posting = (
        await db.execute(select(JobPosting.id).where(JobPosting.id == posting_id))
    ).scalar_one_or_none()
    if posting is None:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} not found")

    row = (
        await db.execute(
            select(ApplicationState).where(ApplicationState.job_posting_id == posting_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = ApplicationState(job_posting_id=posting_id)
        db.add(row)

    row.status = payload.status
    if row.applied_at is None:
        row.applied_at = sa_func.now()

    await db.commit()
    await db.refresh(row)
    return {
        "job_posting_id": str(row.job_posting_id),
        "status": row.status,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── Application resume (feat/application-resume Phase 1) ──────────────────────
# One tailored resume per application, keyed on job_posting_id. Upload a
# .docx/.pdf (raw body — python-multipart isn't a dep) OR paste text (JSON
# body). Same endpoint, dispatched on Content-Type; UPSERTs the row. The file
# streams back from GET so the download goes through the auth proxy like the
# xlsx export. Replaces the global resume_version dropdown (left dormant).

_RESUME_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
_RESUME_MAX_BYTES = 8 * 1024 * 1024  # 8 MB — generous for a resume doc.


def _resume_meta(row: Any) -> dict[str, Any]:
    """Serialize an ApplicationResume row to metadata (never the file bytes)."""
    return {
        "has_file": row.file_blob is not None,
        "file_name": row.file_name,
        "content_type": row.content_type,
        "resume_text": row.resume_text,
        "angle": row.angle,
        "label": row.label,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _safe_filename(name: str) -> str:
    """Strip characters that would break a Content-Disposition header."""
    return name.replace('"', "").replace("\r", "").replace("\n", "").strip() or "resume"


@app.post("/postings/{posting_id}/resume", tags=["public"])
async def upsert_application_resume(
    posting_id: uuid.UUID,
    request: Request,
    db: DbSession,
    filename: Annotated[str | None, Query()] = None,
    angle: Annotated[str | None, Query()] = None,
    label: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Attach/replace THIS application's resume. UPSERT by job_posting_id.

    Two modes, dispatched on Content-Type:
      * ``application/json`` body ``{resume_text?, angle?, label?}`` — paste.
      * any other Content-Type — raw file bytes (``.docx``/``.pdf``);
        ``?filename=`` is required, ``?angle=``/``?label=`` optional.

    404 if the posting doesn't exist; 422 on bad input; 413 if the file
    exceeds the size cap.
    """
    import os

    from sqlalchemy import select

    from job_assist.db.models import ApplicationResume, JobPosting

    posting = (
        await db.execute(select(JobPosting.id).where(JobPosting.id == posting_id))
    ).scalar_one_or_none()
    if posting is None:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} not found")

    row = (
        await db.execute(
            select(ApplicationResume).where(ApplicationResume.job_posting_id == posting_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = ApplicationResume(job_posting_id=posting_id)
        db.add(row)

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type == "application/json":
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="JSON body must be an object")
        if "resume_text" in payload:
            text = payload["resume_text"]
            if text is not None and not isinstance(text, str):
                raise HTTPException(status_code=422, detail="resume_text must be a string or null")
            row.resume_text = text
        if "angle" in payload:
            row.angle = payload["angle"]
        if "label" in payload:
            row.label = payload["label"]
        if row.resume_text is None and row.file_blob is None:
            raise HTTPException(status_code=422, detail="provide a file or non-empty resume_text")
    else:
        body = await request.body()
        if not body:
            raise HTTPException(status_code=422, detail="empty request body")
        if len(body) > _RESUME_MAX_BYTES:
            raise HTTPException(status_code=413, detail="file too large (max 8 MB)")
        if not filename:
            raise HTTPException(
                status_code=422, detail="filename query param is required for a file upload"
            )
        ext = os.path.splitext(filename.lower())[1]
        if ext not in _RESUME_CONTENT_TYPES:
            raise HTTPException(status_code=422, detail="only .docx or .pdf files are accepted")
        row.file_blob = body
        row.file_name = filename
        row.content_type = _RESUME_CONTENT_TYPES[ext]
        if angle is not None:
            row.angle = angle
        if label is not None:
            row.label = label

    await db.commit()
    await db.refresh(row)
    return _resume_meta(row)


@app.get("/postings/{posting_id}/resume", tags=["public"])
async def download_application_resume(posting_id: uuid.UUID, db: DbSession) -> Response:
    """Stream the attached resume file (Content-Disposition: attachment).

    404 if the posting has no attached FILE (a paste-only resume has no blob).
    Routed through the /api/be proxy on the frontend, so the auth token stays
    server-side — same pattern as the xlsx export.
    """
    from sqlalchemy import select

    from job_assist.db.models import ApplicationResume

    row = (
        await db.execute(
            select(ApplicationResume).where(ApplicationResume.job_posting_id == posting_id)
        )
    ).scalar_one_or_none()
    if row is None or row.file_blob is None:
        raise HTTPException(status_code=404, detail="no resume file attached to this posting")

    filename = _safe_filename(row.file_name or "resume")
    return Response(
        content=row.file_blob,
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/companies", tags=["public"])
async def list_companies(
    db: DbSession,
    tier: Annotated[list[int] | None, Query()] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated list of target_company rows with per-row posting counts.

    Single SELECT with three correlated scalar subqueries
    (total_postings, active_postings, ats_set) so we don't N+1 the
    counts. Plus one COUNT(*) for the pagination total = 2 queries.

    Default sort: ``tier ASC NULLS LAST, name ASC``.

    TODO: add authentication before exposing publicly.
    """
    from sqlalchemy import distinct, func, select

    from job_assist.db.models import JobPosting, OutcomeEvent, PostingSource, TargetCompany

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    where_clauses: list[Any] = []
    if tier:
        where_clauses.append(TargetCompany.tier.in_(tier))

    count_stmt = select(func.count()).select_from(TargetCompany)
    for clause in where_clauses:
        count_stmt = count_stmt.where(clause)
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    total_postings = (
        select(func.count(JobPosting.id))
        .where(JobPosting.target_company_id == TargetCompany.id)
        .correlate(TargetCompany)
        .scalar_subquery()
        .label("total_postings")
    )
    active_postings = (
        select(func.count(JobPosting.id))
        .where(JobPosting.target_company_id == TargetCompany.id)
        .where(JobPosting.closed_at.is_(None))
        .correlate(TargetCompany)
        .scalar_subquery()
        .label("active_postings")
    )
    # array_agg returns NULL for empty input — handled in Python below.
    ats_set = (
        select(func.array_agg(distinct(PostingSource.ats)))
        .select_from(PostingSource.__table__)
        .join(JobPosting.__table__, JobPosting.id == PostingSource.job_posting_id)
        .where(JobPosting.target_company_id == TargetCompany.id)
        .correlate(TargetCompany)
        .scalar_subquery()
        .label("ats_set")
    )
    # feat/applied-company-tracking: how many times the operator applied here
    # (linked application_confirmation outcomes) + when last. Single source of
    # truth is outcome_event — counts are derived, never denormalised.
    application_count = (
        select(func.count(OutcomeEvent.id))
        .where(OutcomeEvent.target_company_id == TargetCompany.id)
        .where(OutcomeEvent.outcome_type == "application_confirmation")
        .correlate(TargetCompany)
        .scalar_subquery()
        .label("application_count")
    )
    last_applied_at = (
        select(func.max(OutcomeEvent.received_at))
        .where(OutcomeEvent.target_company_id == TargetCompany.id)
        .where(OutcomeEvent.outcome_type == "application_confirmation")
        .correlate(TargetCompany)
        .scalar_subquery()
        .label("last_applied_at")
    )

    rows_stmt = (
        select(
            TargetCompany,
            total_postings,
            active_postings,
            ats_set,
            application_count,
            last_applied_at,
        )
        .order_by(TargetCompany.tier.asc().nulls_last(), TargetCompany.name.asc())
        .limit(limit)
        .offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)

    rows = (await db.execute(rows_stmt)).all()

    items: list[dict[str, Any]] = []
    for tc, total_count, active_count, ats_arr, applied_count, last_applied in rows:
        items.append(
            {
                "id": str(tc.id),
                "name": tc.name,
                "domain": tc.domain,
                "description": tc.description,
                "tier": tc.tier,
                "ats_set": sorted(str(x) for x in (ats_arr or []) if x),
                "active_postings": int(active_count or 0),
                "total_postings": int(total_count or 0),
                # PR #71: surface fields needed for the Companies page
                # paused-state badge. ``ats_handle`` is NULL when the
                # operator has soft-paused a company (PR #65 Atlassian
                # case); ``ats`` is the canonical adapter, distinct from
                # ``ats_set`` which is what postings actually surfaced.
                # ``notes`` carries the human reason for any pause.
                # Defensive: ``tc.ats`` is an ``ATS`` enum in production
                # but the test helper instantiates TargetCompany with a
                # plain string and SQLAlchemy doesn't always coerce
                # pre-commit. Handle both shapes.
                "ats": (
                    tc.ats.value if tc.ats is not None and hasattr(tc.ats, "value") else tc.ats
                ),
                "ats_handle": tc.ats_handle,
                "notes": tc.notes,
                # feat/applied-company-tracking: provenance + application activity.
                "source": tc.source,
                "application_count": int(applied_count or 0),
                "last_applied_at": (last_applied.isoformat() if last_applied is not None else None),
            }
        )

    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/companies/repeat-signals", tags=["public"])
async def company_repeat_signals(db: DbSession) -> dict[str, Any]:
    """Per-company application-awareness counts from the Gmail outcome history
    (feat/company-app-awareness).

    Returns ``{"signals": {norm_name: {"rejections": r, "active_apps": a,
    "contact_count": c, "display_name": str}}}`` for every company attributable
    from the outcome history or the contacts book with any count >= 1. Keyed by
    the NORMALIZED company name (so "Stripe, Inc." and "stripe" collapse);
    matched on name (linked ``target_company.name``, the name extracted from the
    email subject, or a contact's ``current_employer``), capturing the unlinked
    majority. Ambiguous names are suppressed. ``contact_count``
    (feat/warm-path-badge) counts non-archived contacts whose current employer is
    this company — the operator's warm path in.

    The triage UI keys badges by the posting's normalized company name and
    applies the 1-2 neutral / >=3 amber threshold client-side.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from job_assist.services.company_signals import compute_repeat_signals

    return {"signals": await compute_repeat_signals(db)}


@app.get("/outcomes", tags=["public"])
async def list_outcomes(
    db: DbSession,
    posting_id: uuid.UUID | None = None,
    job_related: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated list of outcome events, sorted chronologically (ASC).

    Optionally narrows to one posting via ``?posting_id=...``. Feeds the
    Applied-page timeline UI and the Pipeline kanban.

    ``?job_related=true`` excludes the ``unrelated`` / ``unclassified`` noise
    rows (~1,884 → ~197 in prod) — the Pipeline only renders lifecycle
    outcomes. Each row carries the fields the Pipeline needs to label a card
    without a per-posting link: ``company_name`` (LEFT JOIN ``target_company``,
    usually NULL — ``job_posting_id`` / ``target_company_id`` are mostly
    unset), ``subject``, ``from_domain``, and ``email_thread_id`` (the
    client-side group key). The client derives the card label from
    ``company_name`` → subject-extraction → ``from_domain``.

    TODO: add authentication before exposing publicly.
    """
    from sqlalchemy import func, select

    from job_assist.db.models import (
        ApplicationState,
        JobPosting,
        OutcomeEvent,
        TargetCompany,
    )

    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    where_clauses: list[Any] = []
    if posting_id is not None:
        where_clauses.append(OutcomeEvent.job_posting_id == posting_id)
    if job_related:
        # Pipeline lifecycle only — drop the classifier's noise buckets.
        where_clauses.append(OutcomeEvent.outcome_type.not_in(["unrelated", "unclassified"]))

    count_stmt = select(func.count()).select_from(OutcomeEvent)
    for clause in where_clauses:
        count_stmt = count_stmt.where(clause)
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    # LEFT JOIN target_company so a linked row can surface its real name; the
    # join column is NULL for the unlinked majority (which the client labels
    # from the subject instead).
    # feat/applied-unified: LEFT JOIN the linked posting (job_posting_id, set
    # ONLY by the #162 no-fanout matcher — never company-level) so each Gmail
    # row can carry (a) the real role title and (b) the manual application_state
    # overlay. Both join columns are NULL for the unlinked majority. These feed
    # the unified Applied view's manual-vs-Gmail resolution (manual wins where
    # set); the join is posting-specific by construction, so it CANNOT
    # reintroduce the company-level fanout bug (#157).
    rows_stmt = (
        select(
            OutcomeEvent,
            TargetCompany.name,
            ApplicationState.status,
            JobPosting.normalized_title,
        )
        .outerjoin(TargetCompany, OutcomeEvent.target_company_id == TargetCompany.id)
        .outerjoin(JobPosting, OutcomeEvent.job_posting_id == JobPosting.id)
        .outerjoin(
            ApplicationState,
            OutcomeEvent.job_posting_id == ApplicationState.job_posting_id,
        )
        .order_by(OutcomeEvent.received_at.asc())
        .limit(limit)
        .offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)
    rows = (await db.execute(rows_stmt)).all()

    items = [
        {
            "id": str(o.id),
            "posting_id": str(o.job_posting_id) if o.job_posting_id else None,
            # feat/applied-company-tracking: company linkage drives the
            # Companies OUTCOMES column (posting_id is uniformly NULL).
            "target_company_id": (str(o.target_company_id) if o.target_company_id else None),
            "received_at": o.received_at.isoformat(),
            "stage": _enum_value(o.outcome_type),
            "confidence": o.classifier_confidence,
            # Pipeline card fields (feat/pipeline-outcome-cards):
            "company_name": company_name,
            "subject": o.subject,
            "from_domain": o.from_domain,
            "email_thread_id": o.email_thread_id,
            # feat/pipeline-detail: the ~200-char Gmail preview (no body is
            # stored) — shown in the Pipeline card detail panel.
            "raw_snippet": o.raw_snippet,
            # feat/applied-unified: posting-specific overlay (NULL unless this
            # email was matched to ONE corpus posting via #162). ``posting_title``
            # is the real role; ``manual_status`` is the authoritative manual
            # application_state on that posting (manual overrides Gmail stage in
            # the unified Applied view).
            "posting_title": posting_title,
            "manual_status": _enum_value(manual_status),
        }
        for (o, company_name, manual_status, posting_title) in rows
    ]

    return {"total": total, "offset": offset, "limit": limit, "items": items}


# ── Public stats endpoints (PR #30b) ──────────────────────────────────────────
#
# Read-only aggregations over `posting_action` history (and
# `job_posting.first_seen_at` for the SURFACED stage). Both endpoints
# share the same time-window contract; see
# `services/stats_windows.py` for the default / validation rules.
#
# TODO: add authentication before exposing publicly (same trust model
# as the rest of /postings, /companies, /outcomes).


@app.get("/stats/calibration", tags=["public"])
async def get_stats_calibration(
    db: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    """KPIs + top rejected role families over a time window.

    See ``services/stats.py`` for the stage-counting rules. Issues 2
    SQL queries: one multi-FILTER aggregation row, one GROUP BY for
    the top role families.
    """
    from job_assist.services.stats import get_calibration
    from job_assist.services.stats_windows import validate_window

    s, u = validate_window(since, until)
    return await get_calibration(db, s, u)


@app.get("/stats/funnel", tags=["public"])
async def get_stats_funnel(
    db: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    """Funnel stages + conversion rates over a time window.

    Stages always returned in the order ``[surfaced, interested,
    applied]``; rates are pairwise across adjacent stages with ``null``
    when the upstream count is 0. Issues 1 SQL query.
    """
    from job_assist.services.stats import get_funnel
    from job_assist.services.stats_windows import validate_window

    s, u = validate_window(since, until)
    return await get_funnel(db, s, u)


@app.get("/stats/ingest", tags=["public"])
async def get_stats_ingest(db: DbSession, days: int = 14) -> dict[str, Any]:
    """Ingest health for the Stats panel (feat/ingest-visibility): daily
    SUM(postings_new) over the last ``days`` (1-30), per-source last run status,
    and success/fail totals — a read layer over the existing ``ingest_run``
    audit table so the operator can SEE whether ingestion is landing postings.
    Pure SELECTs; no writes.
    """
    from job_assist.services.ingest_stats import ingest_daily_stats

    if days < 1 or days > 30:
        raise HTTPException(status_code=422, detail="days must be 1..30")
    return await ingest_daily_stats(db, days=days)


# fix(audit): the /admin/cron-status stub + cron-health.yml dead-man's switch
# are DELETED. The stub returned {"status": "ok"} unconditionally, so the
# workflow's "verify yesterday's crons completed" check was a permanent
# false-green — a fake monitor is worse than none. /admin/ingest/health now
# carries real per-pipeline checks (curated/broad/warm-path/llm/gmail) and
# ingest-health.yml is the alerting cron.


@app.get("/admin/ingest/runs", tags=["admin"])
async def list_ingest_runs(
    db: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Recent ``ingest_run`` rows, newest first (the audit-log view) — started/
    finished, source, status, fetched/new/updated counts, and error_message.
    Optional ``?since=`` floor. Pure SELECT; no writes.
    """
    from job_assist.services.ingest_stats import recent_runs

    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    items = await recent_runs(db, since=since, limit=limit)
    return {"total": len(items), "items": items}


# How recent a successful run / broad sweep must be, and the starvation window.
_HEALTH_RECENT_HOURS = 26
_HEALTH_STARVATION_DAYS = 3
_HEALTH_MIN_NEW_ROLES = 1

# LLM (Gemini) health. The classifier sweep runs daily, so >24h since the most
# recent LLM-driven write (classified_at / embedded_at) means it stalled →
# YELLOW. Exhausted embedding errors (a row that burned all its attempts and
# still has no vector + a stored error) are the queryable proxy for "LLM calls
# are failing" → YELLOW; a large pile of them is a hard outage → RED.
_HEALTH_LLM_STALE_HOURS = 24
_HEALTH_LLM_HARD_ERRORS = 25

# Gmail sweep health. The gmail-poll cron fires every 6h, so >13h since the last
# sweep started = two consecutive polls missed → the Gmail feed has stalled
# (SOFT/yellow). A last sweep that ended in ``failed`` is also SOFT — a single
# secondary-feed hiccup shouldn't red the whole dot.
_HEALTH_GMAIL_STALE_HOURS = 13

# Warm-path sweep health (feat/warm-path-ingest). The warm-path cohort is swept
# WEEKLY (warm-path-ingest.yml, Sundays) — so its freshness window is ~9 days
# (7-day cadence + grace), NOT the 26h ingest window. Deliberately its own
# check: the broad_fresh logic must never false-alarm on a weekly cadence, and
# the check passes trivially when no warm_path companies exist (pre-seeding).
_HEALTH_WARM_PATH_STALE_DAYS = 9

# Wellfound sweep health (feat/wellfound-cron-health). The Wellfound cohort is
# swept DAILY (ingest-daily.yml), but the clearpath actor is variable (~80%
# single-run success) — so a SINGLE bad/missed run must NOT yellow the dot. A
# ~3-day window (daily cadence + grace) means only SUSTAINED failure (three
# consecutive days with no successful sweep) trips it. Shells stamp
# last_swept_at only on non-failed runs, so a failed day leaves the prior
# stamp standing — exactly the sustained-failure semantics we want. Soft
# (yellow), and trivially healthy while no wellfound companies exist.
_HEALTH_WELLFOUND_STALE_DAYS = 3


@app.get("/admin/ingest/health", tags=["admin"])
async def ingest_health(db: DbSession) -> dict[str, Any]:
    """Dead-man's-switch health verdict for the whole ingest pipeline. Pure SELECT.

    The ``ingest-health`` cron curls this daily and alerts when ``ok`` is false,
    so the operator never has to manually check whether crawling still works.
    Per-pipeline checks (fix/audit health split — each cron gets its OWN
    freshness check, so a dead curated cron goes red even while broad
    succeeds), each mapping to a real failure mode:

      * ``curated_fresh`` — a curated-cohort company was swept within
        ``_HEALTH_RECENT_HOURS`` (MAX(last_swept_at) over source='curated';
        proves the daily CURATED cron specifically ran). HARD/red.
      * ``no_hard_failures`` — zero ``failed`` runs in that window
        (``handle_not_found`` is a stale-board signal — surfaced, not a failure).
      * ``broad_fresh`` — the broad pipeline RAN WITHOUT ERROR: a
        ``discovered_handle`` was swept in the window, OR the weekly qualified
        cap is already met (the runner's cap no-op is healthy by design —
        "did it find work" is the starvation check's job, not this one's).
      * ``warm_path_fresh`` — the weekly alumni-cohort sweep ran within
        ~9 days (its own cadence, its own check).
      * ``not_starved`` — at least ``_HEALTH_MIN_NEW_ROLES`` net-new postings over
        the last ``_HEALTH_STARVATION_DAYS`` (catches "the well ran dry").
      * ``llm_healthy`` — the classifier stamped recently OR its candidate
        bucket is empty (a no-op day is GREEN — yellow only when work is
        pending and nothing ran), AND embeddings aren't piling up errors.
      * ``gmail_healthy`` — a Gmail sweep started within ``_HEALTH_GMAIL_STALE_HOURS``
        and the last one didn't fail (metrics carry its runtime).
    """
    from datetime import timedelta

    from sqlalchemy import func, select

    from job_assist.db.models import (
        DiscoveredHandle,
        GmailSweepRun,
        IngestRun,
        JobPosting,
        TargetCompany,
    )

    now = datetime.now(tz=UTC)
    recent_cutoff = now - timedelta(hours=_HEALTH_RECENT_HOURS)
    starve_cutoff = now - timedelta(days=_HEALTH_STARVATION_DAYS)

    last_success = (
        await db.execute(
            select(func.max(IngestRun.finished_at)).where(IngestRun.status == "success")
        )
    ).scalar_one_or_none()
    # fix(audit health split): the curated pipeline gets its OWN freshness
    # check — pre-split, ANY successful ingest_run (broad, warm-path)
    # satisfied "the daily curated cron ran", a false-green. Mirrors the
    # warm_path_fresh pattern: MAX(last_swept_at) over the cohort, trivially
    # healthy while the cohort is empty. Every adapter path stamps
    # last_swept_at now (services/ingestion.py).
    curated_count_raw, curated_last_swept = (
        await db.execute(
            select(
                func.count(),
                func.max(TargetCompany.last_swept_at),
            ).where(TargetCompany.source == "curated")
        )
    ).one()
    curated_count = int(curated_count_raw or 0)
    curated_fresh = curated_count == 0 or (
        curated_last_swept is not None and curated_last_swept >= recent_cutoff
    )
    failed_recent = (
        await db.execute(
            select(func.count())
            .select_from(IngestRun)
            .where(IngestRun.status == "failed")
            .where(IngestRun.started_at >= recent_cutoff)
        )
    ).scalar_one() or 0
    not_found_recent = (
        await db.execute(
            select(func.count())
            .select_from(IngestRun)
            .where(IngestRun.status == "handle_not_found")
            .where(IngestRun.started_at >= recent_cutoff)
        )
    ).scalar_one() or 0
    broad_last_swept = (
        await db.execute(select(func.max(DiscoveredHandle.last_ingested_at)))
    ).scalar_one_or_none()
    # fix(audit health semantics): broad_fresh = the pipeline RAN WITHOUT
    # ERROR. Once the weekly qualified cap is met, the runner no-ops by
    # design and stamps nothing — pre-fix that false-alarmed yellow every
    # day until the ISO week reset. Cap met ⇒ a run would have been a no-op
    # anyway ⇒ GREEN. "Did it find work" stays the starvation check's job.
    from job_assist.services.broad_ingest import (
        _DEFAULT_WEEKLY_CAP,
        count_qualified_broad_this_week,
    )

    broad_qualified_this_week = await count_qualified_broad_this_week(db)
    broad_cap_met = broad_qualified_this_week >= _DEFAULT_WEEKLY_CAP
    net_new = (
        await db.execute(
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.first_seen_at >= starve_cutoff)
        )
    ).scalar_one() or 0

    # ── LLM (Gemini) health ──────────────────────────────────────────────
    # Last LLM activity = the most recent classifier (classified_at) or embedding
    # (embedded_at) write. Errors = OPEN rows that exhausted their embedding
    # attempts and still have no vector + a stored error (the queryable "calls are
    # failing" proxy — the classifier doesn't persist per-row errors).
    llm_stale_cutoff = now - timedelta(hours=_HEALTH_LLM_STALE_HOURS)
    last_classified = (
        await db.execute(select(func.max(JobPosting.classified_at)))
    ).scalar_one_or_none()
    last_embedded = (
        await db.execute(select(func.max(JobPosting.embedded_at)))
    ).scalar_one_or_none()
    _llm_times = [t for t in (last_classified, last_embedded) if t is not None]
    llm_last_used = max(_llm_times) if _llm_times else None
    llm_errors = (
        await db.execute(
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.closed_at.is_(None))
            .where(JobPosting.embedding_error.is_not(None))
            .where(JobPosting.jd_embedding.is_(None))
            .where(JobPosting.embedding_attempt_count >= settings.embedding_enrich_max_attempts)
        )
    ).scalar_one() or 0

    # fix(audit health semantics): the daily reclassify sweep only stamps
    # classified_at when its candidate bucket (open rows the regex left as
    # 'other'/'unknown') is non-empty — so a no-op day used to read
    # "classifier stalled". Healthy = ran without error: a stale stamp is
    # only a problem when there IS pending work the sweep should have taken.
    # Mirrors the sweep's own only_unclassified WHERE clause.
    from sqlalchemy import or_ as _or

    from job_assist.services.classifier import CLASSIFIER_VERSION as _RECLASSIFY_VERSION

    reclassify_pending = (
        await db.execute(
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.closed_at.is_(None))
            .where(
                _or(
                    cast(JobPosting.role_family, Text) == "other",
                    cast(JobPosting.seniority_level, Text) == "unknown",
                )
            )
            # fix(audit): mirror the sweep's same-version skip — an LLM-
            # confirmed 'other'/'unknown' is NOT pending work (re-running the
            # same model version cannot change it), so it must not hold the
            # llm_healthy check yellow.
            .where(
                _or(
                    JobPosting.classified_at.is_(None),
                    JobPosting.classifier_version.is_(None),
                    JobPosting.classifier_version != _RECLASSIFY_VERSION,
                )
            )
        )
    ).scalar_one() or 0

    classifier_fresh = last_classified is not None and last_classified >= llm_stale_cutoff
    classifier_idle_ok = reclassify_pending == 0
    llm_failing = llm_errors > 0
    llm_hard_down = llm_errors >= _HEALTH_LLM_HARD_ERRORS

    # ── Gmail sweep health ───────────────────────────────────────────────
    # The single most-recent gmail_sweep_run is the source of truth: when did the
    # last sweep start, did it finish, how long did it take, and did it succeed?
    gmail_stale_cutoff = now - timedelta(hours=_HEALTH_GMAIL_STALE_HOURS)
    last_gmail = (
        await db.execute(
            select(
                GmailSweepRun.started_at,
                GmailSweepRun.finished_at,
                GmailSweepRun.status,
            )
            .order_by(GmailSweepRun.started_at.desc())
            .limit(1)
        )
    ).first()
    gmail_last_sweep_at = last_gmail.started_at if last_gmail else None
    gmail_last_status = last_gmail.status if last_gmail else None
    gmail_runtime_seconds: float | None = None
    if last_gmail and last_gmail.finished_at and last_gmail.started_at:
        gmail_runtime_seconds = round(
            (last_gmail.finished_at - last_gmail.started_at).total_seconds(), 1
        )
    gmail_fresh = gmail_last_sweep_at is not None and gmail_last_sweep_at >= gmail_stale_cutoff
    gmail_last_failed = gmail_last_status == "failed"

    # ── Warm-path sweep health (feat/warm-path-ingest) ───────────────────
    # Weekly cadence → ~9-day freshness window over the cohort's last_swept_at.
    # Trivially healthy when the cohort is empty (feature unseeded/retired).
    warm_path_cutoff = now - timedelta(days=_HEALTH_WARM_PATH_STALE_DAYS)
    warm_path_count, warm_path_last_swept = (
        await db.execute(
            select(
                func.count(),
                func.max(TargetCompany.last_swept_at),
            ).where(TargetCompany.source == "warm_path")
        )
    ).one()
    warm_path_count = int(warm_path_count or 0)
    warm_path_fresh = warm_path_count == 0 or (
        warm_path_last_swept is not None and warm_path_last_swept >= warm_path_cutoff
    )

    # ── Wellfound sweep health (feat/wellfound-cron-health) ───────────────
    # Daily cadence, variable actor → ~3-day SUSTAINED-failure window over the
    # cohort's last_swept_at (a single bad/missed day stays green; three
    # consecutive trips it). Trivially healthy when the cohort is empty.
    wellfound_cutoff = now - timedelta(days=_HEALTH_WELLFOUND_STALE_DAYS)
    wellfound_count, wellfound_last_swept = (
        await db.execute(
            select(
                func.count(),
                func.max(TargetCompany.last_swept_at),
            ).where(TargetCompany.source == "wellfound")
        )
    ).one()
    wellfound_count = int(wellfound_count or 0)
    wellfound_fresh = wellfound_count == 0 or (
        wellfound_last_swept is not None and wellfound_last_swept >= wellfound_cutoff
    )

    checks = {
        # fix(audit health split): per-pipeline freshness — the curated cron's
        # own check (HARD). Pre-split, any broad/warm-path success satisfied
        # the old recent_success and a dead curated cron read green.
        "curated_fresh": curated_fresh,
        "no_hard_failures": failed_recent == 0,
        # fix(audit health semantics): swept in-window OR weekly cap met
        # (cap no-op = ran-without-error = green).
        "broad_fresh": broad_cap_met
        or (broad_last_swept is not None and broad_last_swept >= recent_cutoff),
        "not_starved": net_new >= _HEALTH_MIN_NEW_ROLES,
        # feat/llm-health + fix(audit health semantics): fresh stamp OR empty
        # candidate bucket (a no-op day is green), AND embeddings aren't
        # piling up exhausted errors. Soft (yellow) unless errors are severe
        # (hard_down below escalates to red).
        "llm_healthy": (classifier_fresh or classifier_idle_ok) and not llm_failing,
        # feat/gmail-health-check: a Gmail sweep started within the last 13h AND
        # the last one didn't fail. Soft (yellow) — a secondary feed stalling
        # shouldn't red the dot.
        "gmail_healthy": gmail_fresh and not gmail_last_failed,
        # feat/warm-path-ingest: the weekly alumni-cohort sweep ran within
        # ~9 days (trivially true while the cohort is empty). Soft (yellow).
        "warm_path_fresh": warm_path_fresh,
        # feat/wellfound-cron-health: the daily Wellfound sweep succeeded within
        # ~3 days (SUSTAINED-failure window — the variable actor's single bad
        # runs don't trip it; trivially true while the cohort is empty). Soft.
        "wellfound_fresh": wellfound_fresh,
    }
    messages = {
        "curated_fresh": f"curated cron has not swept in the last {_HEALTH_RECENT_HOURS}h "
        f"({curated_count} curated companies; last swept: {curated_last_swept})",
        "no_hard_failures": f"{failed_recent} failed ingest_run(s) in the last "
        f"{_HEALTH_RECENT_HOURS}h",
        "broad_fresh": f"broad-ingest has not swept in the last {_HEALTH_RECENT_HOURS}h "
        f"(last sweep: {broad_last_swept}; {broad_qualified_this_week}/{_DEFAULT_WEEKLY_CAP} "
        f"qualified this week — under cap, so the cron should have run)",
        "not_starved": f"starvation: only {net_new} net-new posting(s) in the last "
        f"{_HEALTH_STARVATION_DAYS} days",
        "llm_healthy": (
            f"classifier sweep has not run in the last {_HEALTH_LLM_STALE_HOURS}h "
            f"with {reclassify_pending} candidate row(s) pending "
            f"(LLM last used: {llm_last_used})"
            if not (classifier_fresh or classifier_idle_ok)
            else f"LLM calls are failing: {llm_errors} postings exhausted their embedding attempts"
        ),
        "gmail_healthy": (
            f"Gmail sweep has not run in the last {_HEALTH_GMAIL_STALE_HOURS}h "
            f"(last sweep: {gmail_last_sweep_at})"
            if not gmail_fresh
            else "the last Gmail sweep failed"
        ),
        "warm_path_fresh": (
            f"warm-path sweep has not run in the last {_HEALTH_WARM_PATH_STALE_DAYS} days "
            f"({warm_path_count} warm-path companies; last swept: {warm_path_last_swept})"
        ),
        "wellfound_fresh": (
            f"Wellfound sweep has not succeeded in the last {_HEALTH_WELLFOUND_STALE_DAYS} days "
            f"({wellfound_count} wellfound companies; last swept: {wellfound_last_swept})"
        ),
    }
    problems = [messages[name] for name, passed in checks.items() if not passed]

    # Three-state severity for the UI health dot. HARD problems (a cron didn't
    # run / a run failed = the instance is erroring) are DOWN/red; SOFT problems
    # (starvation, broad set going stale) are DEGRADED/yellow; otherwise OK/green.
    # The frontend maps an unreachable endpoint to DOWN too — a dead backend
    # must never read green.
    # feat/llm-health: a stale classifier or some failing LLM calls are SOFT
    # (yellow); a large pile of exhausted embedding errors is a hard LLM outage
    # (red).
    # fix(audit health split): a dead CURATED cron is hard/red — even while
    # broad and warm-path succeed.
    hard_down = not checks["curated_fresh"] or not checks["no_hard_failures"] or llm_hard_down
    soft_degraded = (
        not checks["not_starved"]
        or not checks["broad_fresh"]
        or not checks["llm_healthy"]
        # feat/gmail-health-check: a stalled / last-failed Gmail sweep is soft.
        or not checks["gmail_healthy"]
        # feat/warm-path-ingest: a stalled weekly warm-path sweep is soft.
        or not checks["warm_path_fresh"]
        # feat/wellfound-cron-health: a SUSTAINED Wellfound failure is soft —
        # the variable actor must never red the dot on a single bad run.
        or not checks["wellfound_fresh"]
    )
    severity = "down" if hard_down else ("degraded" if soft_degraded else "ok")

    return {
        "ok": not problems,
        "severity": severity,
        "problems": problems,
        "checks": checks,
        "metrics": {
            "last_success_at": last_success.isoformat() if last_success else None,
            "failed_runs_recent": failed_recent,
            "handle_not_found_recent": not_found_recent,
            # fix(audit health split): per-pipeline freshness metrics.
            "curated_companies": curated_count,
            "curated_last_swept_at": (
                curated_last_swept.isoformat() if curated_last_swept else None
            ),
            "broad_last_swept_at": broad_last_swept.isoformat() if broad_last_swept else None,
            "broad_qualified_this_week": broad_qualified_this_week,
            "broad_weekly_cap": _DEFAULT_WEEKLY_CAP,
            "broad_cap_met": broad_cap_met,
            "reclassify_pending": reclassify_pending,
            "net_new_starvation_window": net_new,
            "window_hours": _HEALTH_RECENT_HOURS,
            "starvation_days": _HEALTH_STARVATION_DAYS,
            # feat/llm-health
            "llm_last_used_at": llm_last_used.isoformat() if llm_last_used else None,
            "llm_last_classified_at": last_classified.isoformat() if last_classified else None,
            "llm_last_embedded_at": last_embedded.isoformat() if last_embedded else None,
            "llm_exhausted_errors": llm_errors,
            "llm_stale_hours": _HEALTH_LLM_STALE_HOURS,
            # feat/gmail-health-check: the last Gmail sweep's start, status, and
            # how long it took (None until a finished sweep exists).
            "gmail_last_sweep_at": gmail_last_sweep_at.isoformat() if gmail_last_sweep_at else None,
            "gmail_last_sweep_status": gmail_last_status,
            "gmail_last_sweep_runtime_seconds": gmail_runtime_seconds,
            "gmail_stale_hours": _HEALTH_GMAIL_STALE_HOURS,
            # feat/warm-path-ingest: weekly alumni-cohort sweep freshness.
            "warm_path_companies": warm_path_count,
            "warm_path_last_swept_at": (
                warm_path_last_swept.isoformat() if warm_path_last_swept else None
            ),
            "warm_path_stale_days": _HEALTH_WARM_PATH_STALE_DAYS,
            # feat/wellfound-cron-health: daily Wellfound sweep freshness
            # (sustained-failure window).
            "wellfound_companies": wellfound_count,
            "wellfound_last_swept_at": (
                wellfound_last_swept.isoformat() if wellfound_last_swept else None
            ),
            "wellfound_stale_days": _HEALTH_WELLFOUND_STALE_DAYS,
        },
    }


# ── Broad ingestion (Slice 2: handle discovery + bounded trial) ──────────────


@app.post("/admin/discovered-handles/seed", tags=["admin"])
async def seed_discovered_handles_endpoint(
    rows: list[dict[str, Any]],
    db: DbSession,
) -> dict[str, int]:
    """Seed ``discovered_handle`` rows from a JSON body.

    Body shape: ``[{"ats": "greenhouse", "handle": "stripe"}, ...]``.
    Idempotent — a pair already present is skipped (its lifecycle
    counters survive a re-seed). Mirrors the hand-seed list in
    ``scripts/discover_handles.py`` so the operator can push the trial
    set to production without a DB round-trip::

        curl -X POST -H 'Content-Type: application/json' \\
             -d '[{"ats":"greenhouse","handle":"stripe"}, ...]' \\
             https://<host>/admin/discovered-handles/seed

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from job_assist.services.broad_ingest import seed_discovered_handles

    pairs: list[tuple[str, str]] = []
    for row in rows:
        ats = row.get("ats")
        handle = row.get("handle")
        if not isinstance(ats, str) or not isinstance(handle, str) or not ats or not handle:
            raise HTTPException(
                status_code=400,
                detail=f"each row needs non-empty string 'ats' and 'handle'; got {row!r}",
            )
        pairs.append((ats, handle))

    inserted, skipped = await seed_discovered_handles(db, pairs)
    return {"inserted": inserted, "skipped": skipped, "total": len(pairs)}


@app.post("/admin/broad-ingest/run", tags=["admin"])
async def broad_ingest_run(
    db: DbSession,
    limit: int = 100,
    weekly_cap: int = 100,
) -> dict[str, Any]:
    """Run broad ingestion, bounded by ``limit`` handles + the weekly cap.

    Sweeps active ``discovered_handle`` rows (rotation order:
    least-recently-ingested first), ingesting each with
    ``apply_title_prefilter=True`` (PR #96) so only PM-cluster titles
    enter the DB. Creates a thin ``target_company`` shell per handle.
    Maintains per-handle lifecycle counters (``last_ingested_at``,
    ``consecutive_empty_count``; auto-deactivate after 2 consecutive
    404s or 5 consecutive empty pulls).

    **Weekly cap (Slice 3)** — the load-bearing bound. Once
    ``weekly_cap`` qualified (80+ fit_score) broad roles are banked in
    the current ISO week, the runner STOPS for the week: a no-op at the
    top if already met, or a clean stop between boards once reached. The
    cap counts DISTINCT broad-shell postings by ``first_seen_at`` this
    week, so re-pulls never re-count and the week resets automatically
    on Monday 00:00 UTC. One board may overshoot the cap slightly
    (stop-once-reached, not exactly-N).

    With the cap, broad volume is self-limiting — a daily cron can call
    this against thousands of handles and it banks toward the cap then
    idles, no fan-out needed. No Gemini at ingest (role_family is the
    regex heuristic; classifier sweep stays opt-in).

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be 1..500")
    if weekly_cap < 1 or weekly_cap > 10000:
        raise HTTPException(status_code=422, detail="weekly_cap must be 1..10000")

    from job_assist.services.broad_ingest import run_broad_ingest

    report = await run_broad_ingest(db, limit=limit, weekly_cap=weekly_cap)
    return report.model_dump(mode="json")


# ── Outcome event diagnostics (feat/admin-outcomes-stats) ────────────────────


# ── Resume-version tracking (feat/resume-version-tracking) ───────────────────


@app.post(
    "/admin/resume-versions",
    tags=["admin"],
    responses={409: {"description": "A resume_version with this label already exists"}},
)
async def create_resume_version(
    payload: ResumeVersionCreate,
    db: DbSession,
) -> dict[str, Any]:
    """Register a tailored resume variant (e.g. "betterment-trust-v1").

    The operator creates a version, then tags it onto an application via
    ``resume_version_id`` on ``POST /postings/{id}/state``. ``label`` is
    UNIQUE — a duplicate label returns 409.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from sqlalchemy.exc import IntegrityError

    from job_assist.db.models import ResumeVersion
    from job_assist.schemas.resume_version import ResumeVersionRead

    row = ResumeVersion(
        label=payload.label,
        angle=payload.angle,
        snapshot_text=payload.snapshot_text,
        notes=payload.notes,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"resume_version label {payload.label!r} already exists"
        ) from exc
    await db.refresh(row)
    return ResumeVersionRead.model_validate(row).model_dump(mode="json")


@app.get("/resume-versions", tags=["public"])
async def list_resume_versions(db: DbSession) -> dict[str, Any]:
    """List all resume versions, newest first. Feeds the (future) tag picker."""
    from sqlalchemy import select

    from job_assist.db.models import ResumeVersion
    from job_assist.schemas.resume_version import ResumeVersionRead

    rows = (
        (await db.execute(select(ResumeVersion).order_by(ResumeVersion.created_at.desc())))
        .scalars()
        .all()
    )
    items = [ResumeVersionRead.model_validate(r).model_dump(mode="json") for r in rows]
    return {"total": len(items), "items": items}


@app.get("/admin/resume-analytics", tags=["admin"])
async def get_resume_analytics(db: DbSession) -> dict[str, Any]:
    """Resume-version → outcome analytics (company-level).

    Returns ``by_version`` (applications + companies-rejected/confirmed
    per version), ``funnel`` (per version x outcome_type, how deep the
    pipeline went), and ``ambiguous_companies`` (companies that received
    >1 resume version — outcome attribution there is ambiguous because
    ``outcome_event`` links at company level, not posting level). See
    ``services/resume_analytics.py`` for the attribution caveat.

    TODO: add authentication before exposing publicly. Dev-mode only.
    """
    from job_assist.services.resume_analytics import resume_analytics

    return await resume_analytics(db)


@app.get("/admin/outcomes/stats", tags=["admin"])
async def outcomes_stats(
    db: DbSession,
    target_company_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Aggregate stats over ``outcome_event``. All counts computed in SQL.

    Two modes:

      * No ``target_company_id`` — returns the corpus-wide
        ``OutcomesOverallStats``: total rows, the company-link fill rate
        broken down by ``outcome_type``, and the corpus-wide
        ``job_posting_id`` fill (deferred-by-design per
        ``gmail/backfill.py:9-14``).
      * With ``target_company_id`` — returns ``OutcomesForCompanyStats``:
        per-``outcome_type`` count for that one company.

    Read-only. No writes, no LLM calls. Aggregates only; never returns
    the underlying ``outcome_event`` rows. Lives behind ``/admin/`` like
    the rest of the diagnostic surface (no auth — same single-user dev
    trust model documented on the surrounding endpoints).
    """
    from sqlalchemy import case, func, select

    from job_assist.db.models import OutcomeEvent
    from job_assist.schemas.outcomes_stats import (
        CompanyOutcomeBreakdown,
        OutcomesForCompanyStats,
        OutcomesOverallStats,
        OutcomeTypeFill,
    )

    if target_company_id is not None:
        # Per-company breakdown by outcome_type. Filtered server-side;
        # only the (outcome_type, count) pairs cross the wire.
        rows = (
            await db.execute(
                select(
                    OutcomeEvent.outcome_type,
                    func.count().label("n"),
                )
                .where(OutcomeEvent.target_company_id == target_company_id)
                .group_by(OutcomeEvent.outcome_type)
            )
        ).all()
        breakdown = [CompanyOutcomeBreakdown(outcome_type=ot, count=int(n)) for ot, n in rows]
        return OutcomesForCompanyStats(
            target_company_id=target_company_id,
            total_rows=sum(b.count for b in breakdown),
            by_outcome_type=sorted(breakdown, key=lambda b: -b.count),
        ).model_dump(mode="json")

    # Corpus-wide. Two queries:
    #   (1) GROUP BY outcome_type → conditional count of linked / unlinked.
    #       Single round-trip; CASE WHEN ... THEN 1 END keeps it in SQL.
    #   (2) Two scalar COUNTs for the overall totals + posting-link
    #       diagnostic. Wrapped in one SELECT so it's also one round-trip.
    fill_rows = (
        await db.execute(
            select(
                OutcomeEvent.outcome_type,
                func.count(case((OutcomeEvent.target_company_id.is_not(None), 1))).label("linked"),
                func.count(case((OutcomeEvent.target_company_id.is_(None), 1))).label("unlinked"),
            ).group_by(OutcomeEvent.outcome_type)
        )
    ).all()
    by_type = [
        OutcomeTypeFill(
            outcome_type=ot,
            linked_to_company=int(linked),
            unlinked=int(unlinked),
        )
        for ot, linked, unlinked in fill_rows
    ]
    totals = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count(case((OutcomeEvent.target_company_id.is_not(None), 1))).label(
                    "with_company"
                ),
                func.count(case((OutcomeEvent.job_posting_id.is_not(None), 1))).label(
                    "with_posting"
                ),
            )
        )
    ).one()
    return OutcomesOverallStats(
        total_rows=int(totals.total),
        total_linked_to_company=int(totals.with_company),
        total_linked_to_posting=int(totals.with_posting),
        by_outcome_type=sorted(by_type, key=lambda b: -b.total),
    ).model_dump(mode="json")


@app.get("/admin/diagnostics/outcome-linking", tags=["admin"])
async def outcome_linking_diagnostic(db: DbSession) -> dict[str, Any]:
    """Read-only feedback-loop coverage diagnostic.

    Runs four FIXED aggregate SELECTs (no parameters, no user input — this is a
    named diagnostic, NOT a SQL runner) over ``outcome_event`` /
    ``application_resume`` / ``application_state`` and returns the raw result
    sets so an operator can judge whether the outcome→posting feedback loop has
    enough linked signal. Pure SELECT; no writes.

      * ``q1_overall`` — total outcome_events vs how many link to a job_posting.
      * ``q2_by_outcome_type`` — the same fill rate split by outcome_type.
      * ``q3_complete_triples`` — distinct postings that have BOTH an outcome and
        an attached resume (the rows usable as training signal).
      * ``q4_resume_coverage`` — application_state rows vs how many have a resume.
    """
    from decimal import Decimal

    from sqlalchemy import text

    def _ser(m: Any) -> dict[str, Any]:
        return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(m).items()}

    q1 = (
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS total_outcomes, "
                    "COUNT(job_posting_id) AS linked_to_posting, "
                    "ROUND(100.0 * COUNT(job_posting_id) / NULLIF(COUNT(*), 0), 1) AS pct_linked "
                    "FROM outcome_event"
                )
            )
        )
        .mappings()
        .one()
    )

    q2 = (
        (
            await db.execute(
                text(
                    "SELECT outcome_type, COUNT(*) AS total, "
                    "COUNT(job_posting_id) AS linked, "
                    "ROUND(100.0 * COUNT(job_posting_id) / NULLIF(COUNT(*), 0), 1) AS pct_linked "
                    "FROM outcome_event GROUP BY outcome_type ORDER BY total DESC"
                )
            )
        )
        .mappings()
        .all()
    )

    q3 = (
        await db.execute(
            text(
                "SELECT COUNT(DISTINCT oe.job_posting_id) AS complete_triples "
                "FROM outcome_event oe "
                "JOIN application_resume ar ON ar.job_posting_id = oe.job_posting_id "
                "WHERE oe.job_posting_id IS NOT NULL"
            )
        )
    ).scalar_one()

    q4 = (
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS total_applications, COUNT(ar.id) AS with_resume "
                    "FROM application_state a "
                    "LEFT JOIN application_resume ar ON ar.job_posting_id = a.job_posting_id"
                )
            )
        )
        .mappings()
        .one()
    )

    return {
        "q1_overall": _ser(q1),
        "q2_by_outcome_type": [_ser(r) for r in q2],
        "q3_complete_triples": int(q3),
        "q4_resume_coverage": _ser(q4),
    }


@app.get("/admin/diagnostics/triples", tags=["admin"])
async def application_triples(db: DbSession) -> dict[str, Any]:
    """Read-only corpus-completeness surface: one row per APPLIED posting.

    feat/triple-aware-apply (1b). Assembles the (posting, resume, outcome)
    triple per applied posting so the operator can watch how much complete
    training signal exists AND see the standing "applied but no resume" gap
    list (filter ``resume_attached = false``). Pure SELECT; no writes.

    Membership is ``resolved_status = 'applied'`` — identical to
    ``resolved_status_expr`` / the Applied tab: ``COALESCE(manual
    application_state.status, CASE WHEN the latest posting_action = 'applied'
    THEN 'applied' END)``. This is OPTION (a): latest-action, so an
    applied-THEN-reset posting (latest action 'reset', no manual status)
    resolves to NULL and is correctly EXCLUDED. We deliberately do NOT use a
    raw ``EXISTS posting_action='applied'`` (that would keep reset postings).
    Note: a posting the operator advanced to interview/offer/accepted has a
    manual status other than 'applied' and is therefore not in this literal
    'applied' set — by design, matching the explicit 1b decision.

    The outcome column is a read-only LEFT JOIN LATERAL (latest
    ``outcome_event`` linked by ``job_posting_id``); it is informational and
    NEVER drives membership — preserving the no-fanout firewall. Single SELECT
    (within the 2-query read budget), reusing the lateral pattern already in
    ``latest_action_lateral``.
    """
    from sqlalchemy import text

    rows = (
        (
            await db.execute(
                text(
                    "SELECT "
                    "  jp.id AS posting_id, "
                    "  COALESCE(tc.name, jp.canonical_company_name) AS company, "
                    "  jp.normalized_title AS title, "
                    "  jp.fit_score AS fit_score, "
                    "  ar.id AS resume_id, "
                    "  ar.file_name AS file_name, "
                    "  (ar.resume_text IS NOT NULL AND ar.resume_text <> '') AS has_resume_text, "
                    "  (ar.id IS NOT NULL) AS resume_attached, "
                    "  oe.outcome_type AS outcome_type, "
                    "  oe.received_at AS received_at "
                    "FROM job_posting jp "
                    "LEFT JOIN target_company tc ON tc.id = jp.target_company_id "
                    "LEFT JOIN application_resume ar ON ar.job_posting_id = jp.id "
                    "LEFT JOIN LATERAL ("
                    "  SELECT outcome_type, received_at FROM outcome_event "
                    "  WHERE job_posting_id = jp.id ORDER BY received_at DESC LIMIT 1"
                    ") oe ON true "
                    "WHERE COALESCE("
                    "  (SELECT status FROM application_state "
                    "   WHERE job_posting_id = jp.id LIMIT 1), "
                    "  CASE WHEN (SELECT action_type FROM posting_action "
                    "             WHERE job_posting_id = jp.id "
                    "             ORDER BY created_at DESC LIMIT 1) = 'applied' "
                    "       THEN 'applied' END"
                    ") = 'applied' "
                    "ORDER BY oe.received_at DESC NULLS LAST, jp.fit_score DESC NULLS LAST"
                )
            )
        )
        .mappings()
        .all()
    )

    triples = [
        {
            "posting": {
                "id": str(r["posting_id"]),
                "company": r["company"],
                "title": r["title"],
                "fit_score": r["fit_score"],
            },
            "resume": (
                {
                    "id": str(r["resume_id"]),
                    "file_name": r["file_name"],
                    "has_resume_text": bool(r["has_resume_text"]),
                }
                if r["resume_id"] is not None
                else None
            ),
            "outcome": (
                {
                    "outcome_type": r["outcome_type"],
                    "received_at": r["received_at"].isoformat() if r["received_at"] else None,
                }
                if r["outcome_type"] is not None
                else None
            ),
            "resume_attached": bool(r["resume_attached"]),
        }
        for r in rows
    ]

    total = len(triples)
    with_resume = sum(1 for t in triples if t["resume_attached"])
    with_outcome = sum(1 for t in triples if t["outcome"] is not None)
    complete = sum(1 for t in triples if t["resume_attached"] and t["outcome"] is not None)

    return {
        "summary": {
            "applied_postings": total,
            "with_resume": with_resume,
            "applied_no_resume": total - with_resume,
            "with_outcome": with_outcome,
            "complete_triples": complete,
        },
        "triples": triples,
    }


@app.get("/admin/diagnostics/curated-zero-postings", tags=["admin"])
async def curated_zero_postings(db: DbSession) -> dict[str, Any]:
    """Read-only: why curated target_company rows have ZERO corpus postings.

    SCHEMA NOTE (read before trusting the buckets): ``ingest_run`` is one row
    per (ATS source, invocation) and stores NEITHER target_company_id NOR the
    handle — so a run's status (handle_not_found / failed / success) CANNOT be
    attributed to a specific company. The only reliable per-company crawl
    footprint is ``target_company.last_swept_at``, stamped on BOTH success and
    handle_not_found (the sweep "visited") but NEVER on a generic failure (see
    ingestion.py). The per-company buckets below are derived from that proxy;
    the recent-run status counts in ``source_level_context`` are SOURCE-level
    only and are NOT company-attributable. Pure SELECT; no writes.

      * ``q1_companies`` — every curated company with zero job_posting rows:
        name, ats, ats_handle, tier, source, last_swept_at, and a derived
        ``bucket`` (see below).
      * ``q2_buckets`` — mutually exclusive, priority-ordered:
          - ``excluded_from_plan``  : ats_handle IS NULL OR tier IS NULL — the
            daily ingest plan gates on a non-null handle + tier, so these never
            enter it (checked FIRST, regardless of last_swept_at).
          - ``never_swept``         : in-plan but last_swept_at IS NULL — eligible
            yet never successfully visited (never crawled, or only ever failed —
            failure does not stamp last_swept_at).
          - ``swept_zero_postings`` : last_swept_at IS NOT NULL but still zero
            postings — visited but nothing persisted. Per-company we CANNOT
            tell apart "no open roles right now" vs a stale-handle 404
            (handle_not_found also stamps last_swept_at) vs the Bestiary 5.9
            silent-404. See source_level_context for whether 404s are happening.
      * ``q3_plan_exclusion_crosscheck`` — within the zero-posting curated set,
        how many have ats_handle NULL, tier NULL, or either (the plan gate).
      * ``source_level_context`` — recent (<=30d) ingest_run counts by
        (source, status). SOURCE-level, NOT company-attributable; included only
        so the operator can see whether handle_not_found/failed are occurring
        at all when reading the swept_zero_postings bucket.
    """
    from sqlalchemy import text

    def _ser_row(m: Any) -> dict[str, Any]:
        d = dict(m)
        lsa = d.get("last_swept_at")
        d["last_swept_at"] = lsa.isoformat() if lsa else None
        return d

    # q1 + the per-row bucket, computed in SQL with the documented priority.
    bucket_case = (
        "CASE "
        "  WHEN tc.ats_handle IS NULL OR tc.tier IS NULL THEN 'excluded_from_plan' "
        "  WHEN tc.last_swept_at IS NULL THEN 'never_swept' "
        "  ELSE 'swept_zero_postings' END"
    )
    q1 = (
        (
            await db.execute(
                text(
                    "SELECT tc.name, tc.ats, tc.ats_handle, tc.tier, tc.source, "
                    "  tc.last_swept_at, "
                    f"  {bucket_case} AS bucket "
                    "FROM target_company tc "
                    "WHERE tc.source = 'curated' "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM job_posting jp WHERE jp.target_company_id = tc.id) "
                    "ORDER BY tc.tier NULLS LAST, tc.name"
                )
            )
        )
        .mappings()
        .all()
    )

    q2 = (
        (
            await db.execute(
                text(
                    "SELECT bucket, COUNT(*) AS companies FROM ("
                    "  SELECT "
                    f"    {bucket_case} AS bucket "
                    "  FROM target_company tc "
                    "  WHERE tc.source = 'curated' "
                    "    AND NOT EXISTS ("
                    "      SELECT 1 FROM job_posting jp WHERE jp.target_company_id = tc.id)"
                    ") s GROUP BY bucket ORDER BY companies DESC"
                )
            )
        )
        .mappings()
        .all()
    )

    q3 = (
        (
            await db.execute(
                text(
                    "SELECT "
                    "  COUNT(*) FILTER (WHERE tc.ats_handle IS NULL) AS handle_null, "
                    "  COUNT(*) FILTER (WHERE tc.tier IS NULL) AS tier_null, "
                    "  COUNT(*) FILTER (WHERE tc.ats_handle IS NULL OR tc.tier IS NULL) "
                    "    AS either_null, "
                    "  COUNT(*) AS total_zero_posting_curated "
                    "FROM target_company tc "
                    "WHERE tc.source = 'curated' "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM job_posting jp WHERE jp.target_company_id = tc.id)"
                )
            )
        )
        .mappings()
        .one()
    )

    # SOURCE-level only — NOT company-attributable (ingest_run has no company key).
    ctx = (
        (
            await db.execute(
                text(
                    "SELECT source, status, COUNT(*) AS runs, MAX(finished_at) AS latest "
                    "FROM ingest_run "
                    "WHERE started_at >= now() - interval '30 days' "
                    "GROUP BY source, status ORDER BY source, status"
                )
            )
        )
        .mappings()
        .all()
    )

    def _ser_ctx(m: Any) -> dict[str, Any]:
        d = dict(m)
        latest = d.get("latest")
        d["latest"] = latest.isoformat() if latest else None
        return d

    return {
        "schema_note": (
            "ingest_run has no target_company_id/handle; per-company buckets are "
            "derived from target_company.last_swept_at. source_level_context is "
            "SOURCE-level and NOT company-attributable."
        ),
        "q1_companies": [_ser_row(r) for r in q1],
        "q2_buckets": [dict(r) for r in q2],
        "q3_plan_exclusion_crosscheck": dict(q3),
        "source_level_context": [_ser_ctx(r) for r in ctx],
    }


@app.get("/admin/diagnostics/no-candidate-breakdown", tags=["admin"])
async def no_candidate_breakdown(db: DbSession) -> dict[str, Any]:
    """Read-only: why the outcome→posting matcher's ``no_candidate`` bucket is
    large. Over the same scanned set the matcher uses (``job_posting_id IS NULL``
    AND ``target_company_id IS NOT NULL`` AND a linkable ``outcome_type``), break
    down by whether the resolved company has corpus postings, how recently those
    postings closed relative to the outcome, and the company's source.

      * ``q1_company_posting_coverage`` — outcomes whose company has ZERO corpus
        postings vs ≥1 (distinguishes "never crawled" from "window too short").
      * ``q2_recency_for_companies_with_postings`` — for the ≥1 set: has an open
        posting / most-recent close ≤90d / 90-180d / >180d before received_at
        (the 90-180d bucket = what widening the window would recover).
      * ``q3_by_company_source`` — source breakdown of the scanned companies
        (applied vs curated/etc.).

    Fixed diagnostic queries (the type list is bound from the matcher's
    ``_LINKABLE_TYPES`` so it can't drift). Pure SELECT; no writes.
    """
    from sqlalchemy import text

    from job_assist.services.outcome_posting_match import _LINKABLE_TYPES

    params = {"types": list(_LINKABLE_TYPES)}
    scanned = (
        "WITH scanned AS ("
        " SELECT oe.id, oe.target_company_id, oe.received_at FROM outcome_event oe"
        " WHERE oe.job_posting_id IS NULL AND oe.target_company_id IS NOT NULL"
        " AND oe.outcome_type = ANY(:types)) "
    )

    q1 = (
        (
            await db.execute(
                text(
                    scanned + "SELECT COUNT(*) AS scanned,"
                    " COUNT(*) FILTER (WHERE p.n IS NULL OR p.n = 0) AS company_zero_postings,"
                    " COUNT(*) FILTER (WHERE p.n >= 1) AS company_has_postings"
                    " FROM scanned s LEFT JOIN LATERAL (SELECT COUNT(*) n FROM job_posting jp"
                    " WHERE jp.target_company_id = s.target_company_id) p ON TRUE"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    q2 = (
        (
            await db.execute(
                text(
                    scanned + ", best AS (SELECT s.id, s.received_at,"
                    " bool_or(jp.closed_at IS NULL) AS has_open, MAX(jp.closed_at) AS latest_close"
                    " FROM scanned s JOIN job_posting jp ON jp.target_company_id = s.target_company_id"
                    " GROUP BY s.id, s.received_at)"
                    " SELECT COUNT(*) AS with_postings,"
                    " COUNT(*) FILTER (WHERE has_open) AS has_open_posting,"
                    " COUNT(*) FILTER (WHERE NOT has_open AND latest_close >="
                    " received_at - INTERVAL '90 days') AS closed_le_90d,"
                    " COUNT(*) FILTER (WHERE NOT has_open AND latest_close <"
                    " received_at - INTERVAL '90 days' AND latest_close >="
                    " received_at - INTERVAL '180 days') AS closed_90_180d,"
                    " COUNT(*) FILTER (WHERE NOT has_open AND latest_close <"
                    " received_at - INTERVAL '180 days') AS closed_gt_180d"
                    " FROM best"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    q3 = (
        (
            await db.execute(
                text(
                    scanned + "SELECT tc.source,"
                    " COUNT(DISTINCT s.target_company_id) AS companies, COUNT(*) AS outcomes"
                    " FROM scanned s JOIN target_company tc ON tc.id = s.target_company_id"
                    " GROUP BY tc.source ORDER BY outcomes DESC"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    return {
        "q1_company_posting_coverage": dict(q1),
        "q2_recency_for_companies_with_postings": dict(q2),
        "q3_by_company_source": [dict(r) for r in q3],
    }


@app.get("/admin/diagnostics/resume-storage", tags=["admin"])
async def resume_storage_diagnostic(db: DbSession) -> dict[str, Any]:
    """Read-only: where uploaded resumes actually live.

    Resumes attach via ``POST /postings/{id}/resume`` → ``application_resume``
    (keyed on job_posting_id, holds the file blob), INDEPENDENT of
    ``application_state`` (written only by ``PUT /postings/{id}/status`` + the
    Gmail backfill) and of the apply action (``posting_action``, action_type=
    'applied'). ``resume_version`` is the LEGACY label-only pool referenced by
    ``posting_action.resume_version_id``. Fixed diagnostic SELECTs; no writes.

      * ``counts`` — row counts of all four tables (+ applied posting_actions).
      * ``resume_version_rows`` — the legacy pool, with whether each is
        referenced by an applied posting_action (capped at 100).
      * ``application_resume_rows`` — the real uploaded resumes, with whether
        each posting has an 'applied' posting_action (capped at 100).
    """
    from datetime import datetime as _dt
    from uuid import UUID as _UUID

    from sqlalchemy import text

    def _ser(m: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in dict(m).items():
            if isinstance(v, _UUID):
                out[k] = str(v)
            elif isinstance(v, _dt):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out

    counts = (
        (
            await db.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM resume_version) AS resume_version,"
                    " (SELECT COUNT(*) FROM application_resume) AS application_resume,"
                    " (SELECT COUNT(*) FROM application_state) AS application_state,"
                    " (SELECT COUNT(*) FROM posting_action WHERE action_type = 'applied')"
                    " AS posting_action_applied"
                )
            )
        )
        .mappings()
        .one()
    )

    rv_rows = (
        (
            await db.execute(
                text(
                    "SELECT rv.id, rv.label, rv.angle, rv.created_at,"
                    " EXISTS (SELECT 1 FROM posting_action pa WHERE pa.resume_version_id = rv.id)"
                    " AS referenced_by_apply"
                    " FROM resume_version rv ORDER BY rv.created_at LIMIT 100"
                )
            )
        )
        .mappings()
        .all()
    )

    ar_rows = (
        (
            await db.execute(
                text(
                    "SELECT ar.id, ar.job_posting_id, ar.file_name, ar.content_type, ar.created_at,"
                    " (ar.file_blob IS NOT NULL) AS has_file_blob,"
                    " EXISTS (SELECT 1 FROM posting_action pa WHERE pa.job_posting_id = ar.job_posting_id"
                    " AND pa.action_type = 'applied') AS has_applied_action"
                    " FROM application_resume ar ORDER BY ar.created_at LIMIT 100"
                )
            )
        )
        .mappings()
        .all()
    )

    return {
        "counts": dict(counts),
        "resume_version_rows": [_ser(r) for r in rv_rows],
        "application_resume_rows": [_ser(r) for r in ar_rows],
    }


@app.get("/admin/diagnostics/rag-corpus", tags=["admin"])
async def rag_corpus_diagnostic(db: DbSession) -> dict[str, Any]:
    """Read-only: quantify the (posting + resume + outcome) RAG-corpus viability.

    Fixed diagnostic SELECTs (no params, no user input); pure SELECT, no writes.

      * ``q1_table_baselines`` — row counts of the four tables.
      * ``q2_apply_plus_resume`` — distinct job_posting_id with BOTH an 'applied'
        posting_action AND an application_resume row.
      * ``q3_complete_triples`` — of q2's postings, how many also have ≥1
        outcome_event linked (job_posting_id set), with a per-outcome_type
        breakdown (distinct triple postings carrying each type).
      * ``q4_resume_text_availability`` — application_resume rows with non-empty
        resume_text vs file_blob-only (can we embed directly or need extraction).
    """
    from sqlalchemy import text

    q1 = (
        (
            await db.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM resume_version) AS resume_version,"
                    " (SELECT COUNT(*) FROM application_resume) AS application_resume,"
                    " (SELECT COUNT(*) FROM application_state) AS application_state,"
                    " (SELECT COUNT(*) FROM posting_action WHERE action_type = 'applied')"
                    " AS posting_action_applied"
                )
            )
        )
        .mappings()
        .one()
    )

    q2 = (
        await db.execute(
            text(
                "SELECT COUNT(DISTINCT ar.job_posting_id) AS apply_plus_resume"
                " FROM application_resume ar"
                " WHERE EXISTS (SELECT 1 FROM posting_action pa"
                " WHERE pa.job_posting_id = ar.job_posting_id AND pa.action_type = 'applied')"
            )
        )
    ).scalar_one()

    # The triple set: apply + resume + ≥1 linked outcome.
    triples_cte = (
        "WITH triples AS ("
        " SELECT ar.job_posting_id FROM application_resume ar"
        " WHERE EXISTS (SELECT 1 FROM posting_action pa"
        " WHERE pa.job_posting_id = ar.job_posting_id AND pa.action_type = 'applied')"
        " AND EXISTS (SELECT 1 FROM outcome_event oe"
        " WHERE oe.job_posting_id = ar.job_posting_id)) "
    )
    triple_total = (
        await db.execute(text(triples_cte + "SELECT COUNT(*) AS n FROM triples"))
    ).scalar_one()
    triple_by_type = (
        (
            await db.execute(
                text(
                    triples_cte + "SELECT oe.outcome_type,"
                    " COUNT(DISTINCT oe.job_posting_id) AS triples"
                    " FROM outcome_event oe JOIN triples t ON t.job_posting_id = oe.job_posting_id"
                    " GROUP BY oe.outcome_type ORDER BY triples DESC"
                )
            )
        )
        .mappings()
        .all()
    )

    q4 = (
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS application_resume_total,"
                    " COUNT(*) FILTER (WHERE resume_text IS NOT NULL"
                    " AND length(btrim(resume_text)) > 0) AS with_resume_text,"
                    " COUNT(*) FILTER (WHERE file_blob IS NOT NULL) AS with_file_blob,"
                    " COUNT(*) FILTER (WHERE (resume_text IS NULL OR length(btrim(resume_text)) = 0)"
                    " AND file_blob IS NOT NULL) AS blob_only_no_text"
                    " FROM application_resume"
                )
            )
        )
        .mappings()
        .one()
    )

    return {
        "q1_table_baselines": dict(q1),
        "q2_apply_plus_resume": int(q2),
        "q3_complete_triples": {
            "total": int(triple_total),
            "by_outcome_type": [dict(r) for r in triple_by_type],
        },
        "q4_resume_text_availability": dict(q4),
    }


@app.post("/admin/resumes/extract-text", tags=["admin"])
async def extract_resume_text(
    db: DbSession,
    dry_run: bool = True,
    limit: int | None = None,
    preview_chars: int = 500,
) -> dict[str, Any]:
    """Backfill ``resume_text`` from ``.docx`` ``file_blob``s (Phase 2). Read-mostly.

    Scans ``application_resume`` WHERE ``file_blob IS NOT NULL`` AND
    (``resume_text IS NULL`` OR ``resume_text = ''``) — IDEMPOTENT (rows that
    already have text are excluded, so re-runs skip them). For each ``.docx``
    blob it extracts text (stdlib ``zipfile`` + ElementTree; body paragraphs +
    table cells) and, when ``dry_run=false``, writes it to ``resume_text`` and
    ONLY that column (``updated_at`` auto-bumps by design; ``file_blob`` /
    ``file_name`` / ``content_type`` / ``angle`` / ``label`` are untouched).

    ``dry_run=true`` (DEFAULT) extracts + returns a per-row preview but WRITES
    NOTHING. Non-``.docx`` / corrupt blobs are skipped and reported — they never
    crash the batch. Manual one-shot; NOT wired to any cron or hot path.
    """
    from sqlalchemy import or_, select

    from job_assist.db.models import ApplicationResume
    from job_assist.services.resume_extract import (
        ResumeExtractError,
        extract_docx_text,
        looks_like_docx,
    )

    query = (
        select(ApplicationResume)
        .where(ApplicationResume.file_blob.is_not(None))
        .where(or_(ApplicationResume.resume_text.is_(None), ApplicationResume.resume_text == ""))
        .order_by(ApplicationResume.created_at.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    rows = (await db.execute(query)).scalars().all()

    per_row: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped_non_docx = 0
    written = 0
    for r in rows:
        if not looks_like_docx(r.content_type, r.file_name):
            skipped_non_docx += 1
            failed.append(
                {"id": str(r.id), "file_name": r.file_name, "error": "not a .docx (skipped)"}
            )
            continue
        try:
            text = extract_docx_text(r.file_blob or b"")
        except ResumeExtractError as exc:
            failed.append({"id": str(r.id), "file_name": r.file_name, "error": str(exc)})
            continue
        per_row.append(
            {
                "id": str(r.id),
                "file_name": r.file_name,
                "chars_extracted": len(text),
                "preview": text[: max(0, preview_chars)],
            }
        )
        if not dry_run:
            r.resume_text = text
            written += 1

    if not dry_run:
        await db.commit()

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": len(rows),
        "extracted": len(per_row),
        "skipped_non_docx": skipped_non_docx,
        "failed": failed,
        "per_row": per_row,
    }
    if dry_run:
        result["message"] = (
            "DRY RUN — no resume_text was written. Re-run with dry_run=false to persist."
        )
    else:
        result["written"] = written
    return result


@app.get("/admin/resumes/{application_resume_id}/text", tags=["admin"])
async def read_resume_text(application_resume_id: uuid.UUID, db: DbSession) -> dict[str, Any]:
    """Read back the stored ``resume_text`` for one ``application_resume`` row.

    Read-only — returns the extracted text + metadata so the operator can verify
    extraction quality. Never returns the file blob. 404 if the id is unknown.
    """
    from sqlalchemy import select

    from job_assist.db.models import ApplicationResume

    row = (
        await db.execute(
            select(ApplicationResume).where(ApplicationResume.id == application_resume_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"application_resume {application_resume_id} not found"
        )
    return {
        "id": str(row.id),
        "job_posting_id": str(row.job_posting_id),
        "file_name": row.file_name,
        "content_type": row.content_type,
        "has_file_blob": row.file_blob is not None,
        "char_count": len(row.resume_text or ""),
        "resume_text": row.resume_text,
    }


# ── Company enrichment (PR #27) ───────────────────────────────────────────────


@app.post("/enrichment/companies/sweep", tags=["enrichment"])
async def sweep_companies_endpoint(db: DbSession) -> dict[str, Any]:
    """Run ``sweep_companies`` over every ``target_company`` row.

    Called by the daily ``enrich-companies`` GitHub Actions cron at
    07:00 UTC (one hour after the daily ingest at 06:00 UTC, so newly
    discovered target_companies have time to land first).

    No auth — same trust model as the rest of ``/admin/*`` and
    ``/operator/*`` (single-user dev mode). Add a shared-secret guard
    across the whole admin surface in a future PR.
    """
    from job_assist.services.company_enrichment import sweep_companies

    summary = await sweep_companies(db)
    return {
        "total": summary.total,
        "enriched": summary.enriched,
        "skipped": summary.skipped,
        "no_domain": summary.no_domain,
        "errors": summary.errors,
        "exhausted": summary.exhausted,
        "error_details": summary.error_details,
    }


@app.post("/enrichment/companies/{company_id}/retry", tags=["enrichment"])
async def retry_company_enrichment_endpoint(
    company_id: uuid.UUID,
    db: DbSession,
) -> dict[str, Any]:
    """Reset ``enrichment_attempt_count`` for one company and re-run enrichment.

    For manual recovery from the ``exhausted`` state. Also clears any
    cached description / enriched_at so the next call is a fresh attempt.
    """
    from job_assist.services.company_enrichment import reset_attempts_and_retry

    result = await reset_attempts_and_retry(db, company_id)
    if result.status == "not_found":
        raise HTTPException(
            status_code=404,
            detail=f"target_company id={company_id} not found",
        )
    return {
        "status": result.status,
        "company_id": result.company_id,
        "error": result.error,
    }


@app.post("/enrichment/divisions/sweep", tags=["enrichment"])
async def sweep_divisions_endpoint(db: DbSession) -> dict[str, Any]:
    """Discover (company, dept, team) tuples + enrich each division.

    Called by the daily ``enrich-divisions`` GitHub Actions cron at
    08:00 UTC (one hour after enrich-companies). Discovery is idempotent
    via the ``uq_division_company_dept_team`` UNIQUE NULLS NOT DISTINCT
    constraint; enrichment is idempotent via the per-row ``description
    IS NOT NULL`` skip.

    No auth — same trust model as the rest of /admin and /enrichment.
    TODO: tighten before public exposure.
    """
    from job_assist.services.division_enrichment import sweep_divisions

    summary = await sweep_divisions(db)
    return {
        "discovered": summary.discovered,
        "already_existed": summary.already_existed,
        "total": summary.total,
        "enriched": summary.enriched,
        "skipped": summary.skipped,
        "exhausted": summary.exhausted,
        "missing_context": summary.missing_context,
        "errors": summary.errors,
        "error_details": summary.error_details,
    }


@app.post("/enrichment/divisions/{division_id}/retry", tags=["enrichment"])
async def retry_division_enrichment_endpoint(
    division_id: uuid.UUID,
    db: DbSession,
) -> dict[str, Any]:
    """Reset ``enrichment_attempt_count`` for one division and re-run."""
    from job_assist.services.division_enrichment import reset_attempts_and_retry

    result = await reset_attempts_and_retry(db, division_id)
    if result.status == "not_found":
        raise HTTPException(
            status_code=404,
            detail=f"division id={division_id} not found",
        )
    return {
        "status": result.status,
        "division_id": result.division_id,
        "error": result.error,
    }


# ── JD summary enrichment (PR #41) ────────────────────────────────────────────


@app.post("/enrichment/jd-summaries/sweep", tags=["enrichment"])
async def sweep_jd_summaries_endpoint(
    db: DbSession,
    limit: int = 100,
) -> dict[str, Any]:
    """Run the JD-summary sweep over eligible ``job_posting`` rows.

    Called by the daily ``enrich-jd-summaries`` GitHub Actions cron at
    08:30 UTC (after ingest + company + division enrichment). The sweep
    caps at ``limit`` rows per call so a Gemini RPM hit doesn't drag the
    workflow past its 15-minute timeout. Idempotent — each row's
    ``jd_summary_markdown IS NOT NULL`` short-circuits the LLM call.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from job_assist.services.jd_summary_enrichment import sweep_jd_summaries

    summary = await sweep_jd_summaries(db, limit=limit)
    return {
        "total": summary.total,
        "enriched": summary.enriched,
        "skipped": summary.skipped,
        "exhausted": summary.exhausted,
        "missing_context": summary.missing_context,
        "errors": summary.errors,
        "error_details": summary.error_details,
    }


@app.post(
    "/enrichment/jd-summaries/{posting_id}/retry",
    tags=["enrichment"],
)
async def retry_jd_summary_enrichment_endpoint(
    posting_id: uuid.UUID,
    db: DbSession,
) -> dict[str, Any]:
    """Reset ``jd_summary_enrichment_attempt_count`` and re-run.

    Mirrors the company / division retry endpoints. Clears any cached
    summary so the next call is a fresh attempt.
    """
    from job_assist.services.jd_summary_enrichment import reset_attempts_and_retry

    result = await reset_attempts_and_retry(db, posting_id)
    if result.status == "not_found":
        raise HTTPException(
            status_code=404,
            detail=f"job_posting id={posting_id} not found",
        )
    return {
        "status": result.status,
        "posting_id": result.posting_id,
        "error": result.error,
    }


# ── Semantic embeddings (slice 1, feat/embeddings-slice1) ─────────────────────
# Populate-only + a read-only validation gate. NOTHING here changes scoring:
# score_posting / fit_score / scorer_version / the sort modes / postings_query
# are untouched. These endpoints write/read ONLY the new vector columns.


@app.post("/admin/embeddings/sweep", tags=["admin"])
async def sweep_embeddings_endpoint(
    db: DbSession,
    limit: int = 100,
) -> EmbeddingSweepResponse:
    """Embed up to ``limit`` eligible OPEN postings (gemini-embedding-001).

    Opt-in / cron-driven — embeddings are NOT computed at ingest, so this never
    auto-costs. Idempotent + cache-aware: a row with a fresh vector
    (``jd_text_hash_embedded == jd_text_hash``) short-circuits without an API
    call; a row whose JD text changed is re-embedded.

    NO scoring change — this only populates ``job_posting.jd_embedding``.

    TODO: add authentication before exposing publicly (dev-mode / single-user).
    """
    from job_assist.services.embeddings import sweep_embeddings

    summary = await sweep_embeddings(db, limit=limit)
    return EmbeddingSweepResponse(
        total=summary.total,
        embedded=summary.embedded,
        skipped=summary.skipped,
        exhausted=summary.exhausted,
        missing_context=summary.missing_context,
        errors=summary.errors,
        error_details=summary.error_details,
    )


@app.post("/admin/embeddings/{posting_id}/retry", tags=["admin"])
async def retry_embedding_endpoint(
    posting_id: uuid.UUID,
    db: DbSession,
) -> EmbeddingRetryResponse:
    """Reset ``embedding_attempt_count`` + clear the vector, then re-embed.

    Mirrors the jd-summary / enrichment retry endpoints.
    """
    from job_assist.services.embeddings import reset_attempts_and_retry

    result = await reset_attempts_and_retry(db, posting_id)
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=f"job_posting id={posting_id} not found")
    return EmbeddingRetryResponse(
        status=result.status,
        posting_id=result.posting_id,
        source=result.source,
        error=result.error,
    )


@app.post("/admin/embeddings/recalibrate", tags=["admin"])
async def recalibrate_embeddings_endpoint(db: DbSession) -> dict[str, Any]:
    """Recompute ``job_posting.similarity_score`` (slice 2a) — the calibrated
    0-100 PERCENT_RANK of each embedded posting's cosine-to-profile across the
    corpus. One SQL pass; NO ranking change (similarity_score is materialized
    here, not by score_posting). Fires automatically on the sweep tail + on
    profile change; exposed for manual recalibration / verification.

    Returns the verification gate inline (``distribution`` + ``top_by_similarity``)
    so the 2a spread can be read off this POST — the cached nearest GET can lag
    a deploy by serving a stale replica, but POST reliably hits current code.
    """
    from job_assist.services.embeddings import recalibrate_similarity

    return await recalibrate_similarity(db, include_distribution=True)


@app.post("/admin/embeddings/similarity-distribution", tags=["admin"])
async def similarity_distribution_endpoint(db: DbSession) -> dict[str, Any]:
    """Read-only slice 2a gate: the ``similarity_score`` distribution
    (count / calibrated_count / min / p25 / median / p75 / max over embedded
    open rows) + the top-15 roles by ``similarity_score``. POST (not GET) so it
    isn't served from a stale cached replica during a rollout. Does not write.
    """
    from job_assist.services.embeddings import similarity_distribution

    return await similarity_distribution(db)


@app.get("/admin/embeddings/nearest", tags=["admin"])
async def nearest_embeddings_endpoint(
    db: DbSession,
    n: int = 20,
) -> NearestResponse:
    """Read-only validation gate: the N postings nearest the profile vector by
    cosine similarity.

    The slice-1 go/no-go signal. Returns title / company / cosine_sim /
    heuristic fit_score / embedded_source per row, plus the cosine
    min/median/max spread across all embedded open rows. ``available=False``
    (with a reason) when the profile or corpus isn't embedded yet. Reads only —
    no scoring, no mutation.
    """
    from job_assist.services.embeddings import nearest_postings

    n_clamped = max(1, min(n, 100))
    out = await nearest_postings(db, n=n_clamped)
    return NearestResponse(**out)


# ── Logging setup ─────────────────────────────────────────────────────────────


def _configure_logging() -> None:
    """Configure structured logging."""
    logging.basicConfig(level=settings.log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
            if settings.environment != "development"
            else structlog.dev.ConsoleRenderer(),
        ],
    )


_configure_logging()

# Keep uuid import used by type system (run.id is UUID)
_ = uuid
