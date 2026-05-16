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
