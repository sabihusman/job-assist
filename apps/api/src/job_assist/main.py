"""FastAPI application entry point."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.config import settings
from job_assist.db.session import get_db

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    logger.info("api.startup", environment=settings.environment, version="0.0.1")
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


# ── Admin — ingestion ─────────────────────────────────────────────────────────


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
    from job_assist.adapters.ashby import AshbyAdapter
    from job_assist.adapters.base import Adapter
    from job_assist.adapters.greenhouse import GreenhouseAdapter
    from job_assist.adapters.lever import LeverAdapter
    from job_assist.services.ingestion import IngestionService

    _SUPPORTED = {"greenhouse", "lever", "ashby"}
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
) -> dict[str, int]:
    """Seed target_company rows from a JSON body.

    Idempotent: each row's ``name`` is checked first; existing rows are
    skipped rather than updated. The body is the seed JSON itself, so the
    private seed file (``apps/api/seeds/target_companies.json``) never
    needs to be uploaded to the Railway container — the operator runs::

        curl -X POST -H 'Content-Type: application/json' \\
             -d @apps/api/seeds/target_companies.json \\
             https://<host>/admin/seed/target-companies

    Returns the insert / skip counts so the operator can verify the
    expected number of rows landed.

    TODO: add authentication before exposing this endpoint publicly.
          Currently dev-mode only — single-user deployment.
    """
    from job_assist.seed import seed_from_rows

    try:
        inserted, skipped = await seed_from_rows(db, rows)
    except ValueError as exc:  # malformed row (missing name/tier)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"inserted": inserted, "skipped": skipped, "total": inserted + skipped}


# ── Admin — Gmail backfill ────────────────────────────────────────────────────


@app.post("/admin/gmail/backfill")
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
    missing = [
        name
        for name, value in (
            ("GMAIL_CREDENTIALS_JSON", settings.gmail_credentials_json),
            ("GMAIL_REFRESH_TOKEN", settings.gmail_refresh_token),
            ("GEMINI_API_KEY", settings.gemini_api_key),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Gmail backfill unavailable: missing env var(s) {missing}. "
                "Set these on Railway (or .env locally) and retry."
            ),
        )

    from job_assist.gmail.backfill import run_backfill
    from job_assist.gmail.classifier import EmailClassifier
    from job_assist.gmail.client import GmailClient

    gmail = GmailClient(
        credentials_json=settings.gmail_credentials_json,
        refresh_token=settings.gmail_refresh_token,
    )
    classifier = EmailClassifier(api_key=settings.gemini_api_key)

    report = await run_backfill(db, gmail, classifier, days_back=days)
    return report.model_dump(mode="json")


# ── Admin — cron status ────────────────────────────────────────────────────────


@app.get("/admin/cron-status")
async def cron_status() -> dict[str, str]:
    """Cron health-check endpoint.  Returns ok when the API is reachable."""
    return {"status": "ok"}


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
