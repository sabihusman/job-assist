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
from sqlalchemy import and_, true
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.config import settings
from job_assist.db.session import get_db
from job_assist.schemas.operator_profile import OperatorProfileUpdate

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


# ATS sources the daily cron knows how to ingest. Workday rows are filtered
# out — that adapter doesn't exist yet (Week 2+ roadmap).
_INGESTABLE_ATS = ("greenhouse", "lever", "ashby")


@app.get("/admin/ingest/plan")
async def get_ingest_plan(db: DbSession) -> list[dict[str, str]]:
    """List ``(ats, handle)`` pairs the daily cron should ingest.

    Filters to rows where:
      * ``ats`` is one of the three currently-supported adapters
      * ``ats_handle IS NOT NULL`` (we can't ingest without a handle)
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
            .where(TargetCompany.ats.in_(_INGESTABLE_ATS))
            .where(TargetCompany.ats_handle.isnot(None))
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

    gmail, classifier = _build_gmail_runtime()
    report = await run_backfill(db, gmail, classifier, days_back=days)
    return report.model_dump(mode="json")


@app.post("/admin/gmail/poll")
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

    gmail, classifier = _build_gmail_runtime()
    report = await run_poll(db, gmail, classifier)
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
    return OperatorProfileRead.model_validate(row).model_dump(mode="json")


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


_ALLOWED_ATS_VALUES = {"greenhouse", "lever", "ashby"}
_ALLOWED_REMOTE_TYPES = {"remote", "hybrid", "onsite"}


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
    tier: list[int] | None = None,
    ats: list[str] | None = None,
    remote_type: list[str] | None = None,
    role_family: list[str] | None = None,
    target_company_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated list of postings with the company/role/source nested.

    Default sort: ``first_seen_at DESC``. Total + paginated rows fetched
    in two queries (one COUNT(*), one SELECT with LATERAL JOIN onto the
    most-recent posting_source for the ats + url fields).

    TODO: add authentication before exposing publicly.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased

    from job_assist.db.models import JobPosting, PostingSource, TargetCompany

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    ats = _validate_ats_filter(ats)
    remote_type = _validate_remote_type_filter(remote_type)

    # Build the WHERE clause shared by COUNT and SELECT.
    where_clauses: list[Any] = []
    if tier:
        where_clauses.append(TargetCompany.tier.in_(tier))
    if remote_type:
        where_clauses.append(JobPosting.remote_type.in_(remote_type))
    if role_family:
        # Case-insensitive match per spec.
        lowered = [v.lower() for v in role_family]
        where_clauses.append(func.lower(JobPosting.role_family).in_(lowered))
    if target_company_id is not None:
        where_clauses.append(JobPosting.target_company_id == target_company_id)
    if ats:
        # The ats filter looks at posting_source.ats; reach it through EXISTS
        # so we don't multiply rows in the COUNT.
        ats_exists = (
            select(PostingSource.id)
            .where(PostingSource.job_posting_id == JobPosting.id)
            .where(PostingSource.ats.in_(ats))
            .exists()
        )
        where_clauses.append(ats_exists)

    base_join = JobPosting.__table__.outerjoin(
        TargetCompany.__table__,
        JobPosting.target_company_id == TargetCompany.id,
    )

    # COUNT query — no LATERAL needed.
    count_stmt = select(func.count()).select_from(base_join)
    for clause in where_clauses:
        count_stmt = count_stmt.where(clause)
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    # LATERAL subquery picks the most-recent posting_source per posting in
    # the same execute call as the main SELECT. Keeps the query count at 1
    # for the page even when there are multiple sources per posting.
    ps_alias = aliased(PostingSource)
    recent_ps = (
        select(ps_alias.ats.label("ps_ats"), ps_alias.source_url.label("ps_url"))
        .where(ps_alias.job_posting_id == JobPosting.id)
        .order_by(ps_alias.fetched_at.desc())
        .limit(1)
        .lateral("recent_ps")
    )

    rows_stmt = (
        select(JobPosting, TargetCompany, recent_ps.c.ps_ats, recent_ps.c.ps_url)
        .select_from(base_join)
        .outerjoin(recent_ps, true())
        .order_by(JobPosting.first_seen_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)

    rows = (await db.execute(rows_stmt)).all()

    items: list[dict[str, Any]] = []
    for jp, tc, ps_ats, ps_url in rows:
        salary_block: dict[str, Any] | None = None
        if any(x is not None for x in (jp.salary_min, jp.salary_max, jp.salary_currency)):
            salary_block = {
                "min": jp.salary_min,
                "max": jp.salary_max,
                "currency": jp.salary_currency,
                "period": jp.salary_period.value if jp.salary_period else None,
            }

        items.append(
            {
                "id": str(jp.id),
                "company": {
                    "id": str(tc.id) if tc is not None else None,
                    "name": tc.name if tc is not None else jp.canonical_company_name,
                    "domain": tc.domain if tc is not None else None,
                    "description": tc.description if tc is not None else None,
                    "tier": tc.tier if tc is not None else None,
                },
                "role": {
                    "title": jp.normalized_title,
                    "family": jp.role_family.value if jp.role_family else None,
                    "department": jp.department,
                    "team": jp.team,
                    "seniority": jp.seniority_level.value if jp.seniority_level else None,
                },
                "location_raw": jp.location_raw,
                "locations_normalized": _extract_location_strings(jp.locations_normalized),
                "remote_type": jp.remote_type.value if jp.remote_type else None,
                "salary": salary_block,
                "source": {
                    "ats": str(ps_ats) if ps_ats else "unknown",
                    "url": ps_url,
                },
                "first_seen_at": jp.first_seen_at.isoformat() if jp.first_seen_at else None,
                "score": None,
            }
        )

    return {"total": total, "offset": offset, "limit": limit, "items": items}


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

    from job_assist.db.models import Division, JobPosting, PostingSource, TargetCompany

    ps_alias = aliased(PostingSource)
    recent_ps = (
        select(ps_alias.ats.label("ps_ats"), ps_alias.source_url.label("ps_url"))
        .where(ps_alias.job_posting_id == JobPosting.id)
        .order_by(ps_alias.fetched_at.desc())
        .limit(1)
        .lateral("recent_ps")
    )

    stmt = (
        select(JobPosting, TargetCompany, Division, recent_ps.c.ps_ats, recent_ps.c.ps_url)
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
        .where(JobPosting.id == posting_id)
    )

    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"posting {posting_id} not found")

    jp, tc, div, ps_ats, ps_url = row

    salary_block: dict[str, Any] | None = None
    if any(x is not None for x in (jp.salary_min, jp.salary_max, jp.salary_currency)):
        salary_block = {
            "min": jp.salary_min,
            "max": jp.salary_max,
            "currency": jp.salary_currency,
            "period": jp.salary_period.value if jp.salary_period else None,
        }

    division_block: dict[str, Any] | None = None
    if div is not None:
        division_block = {
            "id": str(div.id),
            "department": div.department,
            "team": div.team,
            "description": div.description,
        }

    return {
        "id": str(jp.id),
        "company": {
            "id": str(tc.id) if tc is not None else None,
            "name": tc.name if tc is not None else jp.canonical_company_name,
            "domain": tc.domain if tc is not None else None,
            "description": tc.description if tc is not None else None,
            "tier": tc.tier if tc is not None else None,
        },
        "role": {
            "title": jp.normalized_title,
            "family": jp.role_family.value if jp.role_family else None,
            "department": jp.department,
            "team": jp.team,
            "seniority": jp.seniority_level.value if jp.seniority_level else None,
        },
        "location_raw": jp.location_raw,
        "locations_normalized": _extract_location_strings(jp.locations_normalized),
        "remote_type": jp.remote_type.value if jp.remote_type else None,
        "salary": salary_block,
        "source": {
            "ats": str(ps_ats) if ps_ats else "unknown",
            "url": ps_url,
        },
        "first_seen_at": jp.first_seen_at.isoformat() if jp.first_seen_at else None,
        "score": None,
        "description_markdown": jp.jd_text or None,
        "division": division_block,
        "posted_at": jp.posted_at.isoformat() if jp.posted_at else None,
        "last_seen_at": jp.last_seen_at.isoformat() if jp.last_seen_at else None,
        "closed_at": jp.closed_at.isoformat() if jp.closed_at else None,
    }


@app.get("/companies", tags=["public"])
async def list_companies(
    db: DbSession,
    tier: list[int] | None = None,
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

    from job_assist.db.models import JobPosting, PostingSource, TargetCompany

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

    rows_stmt = (
        select(TargetCompany, total_postings, active_postings, ats_set)
        .order_by(TargetCompany.tier.asc().nulls_last(), TargetCompany.name.asc())
        .limit(limit)
        .offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)

    rows = (await db.execute(rows_stmt)).all()

    items: list[dict[str, Any]] = []
    for tc, total_count, active_count, ats_arr in rows:
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
            }
        )

    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/outcomes", tags=["public"])
async def list_outcomes(
    db: DbSession,
    posting_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated list of outcome events, sorted chronologically (ASC).

    Optionally narrows to one posting via ``?posting_id=...``. Feeds the
    Applied-page timeline UI.

    TODO: add authentication before exposing publicly.
    """
    from sqlalchemy import func, select

    from job_assist.db.models import OutcomeEvent

    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1..200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    where_clauses: list[Any] = []
    if posting_id is not None:
        where_clauses.append(OutcomeEvent.job_posting_id == posting_id)

    count_stmt = select(func.count()).select_from(OutcomeEvent)
    for clause in where_clauses:
        count_stmt = count_stmt.where(clause)
    total: int = (await db.execute(count_stmt)).scalar_one() or 0

    rows_stmt = (
        select(OutcomeEvent).order_by(OutcomeEvent.received_at.asc()).limit(limit).offset(offset)
    )
    for clause in where_clauses:
        rows_stmt = rows_stmt.where(clause)
    rows = (await db.execute(rows_stmt)).scalars().all()

    items = [
        {
            "id": str(o.id),
            "posting_id": str(o.job_posting_id) if o.job_posting_id else None,
            "received_at": o.received_at.isoformat(),
            "stage": o.outcome_type.value if o.outcome_type else None,
            "confidence": o.classifier_confidence,
        }
        for o in rows
    ]

    return {"total": total, "offset": offset, "limit": limit, "items": items}


# ── Admin — cron status ────────────────────────────────────────────────────────


@app.get("/admin/cron-status")
async def cron_status() -> dict[str, str]:
    """Cron health-check endpoint.  Returns ok when the API is reachable."""
    return {"status": "ok"}


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
