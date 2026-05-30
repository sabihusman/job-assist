"""FastAPI application entry point."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Text, and_, cast, true
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.config import settings
from job_assist.db.session import get_db
from job_assist.schemas.contact import ContactCreate, ContactUpdate
from job_assist.schemas.operator_profile import OperatorProfileUpdate
from job_assist.schemas.outreach import OutreachMessageCreate
from job_assist.schemas.public import DEFAULT_SORT, PostingStateRequest, SortKey
from job_assist.schemas.reclassify import ReclassifySweepRequest, ReclassifySweepResponse
from job_assist.schemas.score import ScoreSweepRequest, ScoreSweepResponse

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


_ALLOWED_CONTACT_SOURCE_TYPES = frozenset(
    {"tippie_alumni", "linkedin_outreach", "recruiter_inbound", "warm_intro"}
)


@app.get("/contacts", tags=["public"])
async def list_contacts(
    db: DbSession,
    source_type: Annotated[list[str] | None, Query()] = None,
    search: str | None = None,
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
    from sqlalchemy import func, or_, select

    from job_assist.db.models import Contact

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

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
    if search:
        pattern = f"%{search.strip().lower()}%"
        if pattern.strip("%"):
            # Match the name fields independently AND a "first last"
            # concatenation so "jane d" finds "Jane Doe". COALESCE
            # guards a NULL preferred_first_name from breaking concat.
            full_name = func.lower(func.concat(Contact.first_name, " ", Contact.last_name))
            where_clauses.append(
                or_(
                    func.lower(Contact.first_name).like(pattern),
                    func.lower(Contact.last_name).like(pattern),
                    full_name.like(pattern),
                )
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

    try:
        gmail, classifier = _build_gmail_runtime()
        report = await run_backfill(db, gmail, classifier, days_back=days)
    except HTTPException:
        raise
    except Exception as exc:
        raise _surface_gmail_failure("/admin/gmail/backfill", exc) from exc
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

    try:
        gmail, classifier = _build_gmail_runtime()
        report = await run_poll(db, gmail, classifier)
    except HTTPException:
        raise
    except Exception as exc:
        raise _surface_gmail_failure("/admin/gmail/poll", exc) from exc
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
    from job_assist.services.classifier import CLASSIFIER_VERSION, classify_posting
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
    # Oldest classified_at first; NULLs sort first so never-LLM-classified
    # rows are processed before rows the sweep has already touched.
    stmt = stmt.order_by(
        JobPosting.classified_at.asc().nulls_first(),
        JobPosting.first_seen_at.asc(),
    ).limit(payload.limit)

    rows = (await db.execute(stmt)).scalars().all()

    # PR #56: load the operator profile once for the post-classification
    # rescoring pass below. NULL profile means the table is unseeded —
    # skip rescoring rather than fail the classifier sweep.
    op_row = await db.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    operator_profile = op_row.scalar_one_or_none()

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
    stmt = (
        select(JobPosting, TargetCompany.tier)
        .outerjoin(TargetCompany, JobPosting.target_company_id == TargetCompany.id)
        # Skip stale/closed postings (Bestiary 5.18) — no point scoring a
        # removed posting that won't surface in Triage anyway.
        .where(JobPosting.closed_at.is_(None))
    )
    if payload.only_unscored:
        stmt = stmt.where(JobPosting.fit_score.is_(None))
    # Stable id ASC tiebreaker on every key (bestiary entry).
    stmt = stmt.order_by(
        JobPosting.scored_at.asc().nulls_first(),
        JobPosting.first_seen_at.asc(),
        JobPosting.id.asc(),
    ).limit(payload.limit)

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
) -> dict[str, Any]:
    """Serialise the LATERAL state row (or NULLs) into a StateEmbedded dict.

    All four columns are NULL together when no posting_action row exists
    for the posting. We surface that as ``current=None`` (still in
    triage) rather than omitting the field, so the frontend can rely on
    the key always being present.
    """
    return {
        "current": _enum_value(action_type),
        "reason": _enum_value(reason),
        "snooze_until": snooze_until.isoformat() if snooze_until else None,
        "current_at": created_at.isoformat() if created_at else None,
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
    per_company_cap: int = 3,
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
    from job_assist.services.postings_query import PostingsViewSpec, build_view_parts

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    if per_company_cap < 0:
        raise HTTPException(
            status_code=422,
            detail="per_company_cap must be >= 0 (0 disables the cap entirely)",
        )
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
                    "tier": tc.tier if tc is not None else None,
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
                "state": _state_block(pa_action_type, pa_reason, pa_snooze_until, pa_created_at),
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
    per_company_cap: int = 3,
    include_closed: bool = False,
    include_filtered: bool = False,
) -> Response:
    """Export the current Triage view as a two-sheet xlsx.

    Same filter / sort / cap vocabulary as ``GET /postings``. Output:
      * Sheet 1 ``Export Context`` — timestamp, corpus size, active
        filters, matched-before-cap count, score range, scorer weights,
        operator hard rules, plain-language notes on the score.
      * Sheet 2 ``Jobs`` — top ``EXPORT_ROW_CAP`` (40) rows by the
        operator-selected sort, with rank, company, role, fit_score and
        its five sub-scores, salary, location, remote_type, tier,
        ats_source, apply_url, first_seen, jd_summary_markdown.

    Cap semantics: the per-company cap is honored exactly as the visible
    view does — exported 40 == visible 40 for the same URL. The
    "matched-before-cap" count on Sheet 1 reports how many would have
    surfaced without the cap so the reviewer can sense the funnel.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import aliased

    from job_assist.db.models import JobPosting, OperatorProfile, PostingSource, TargetCompany
    from job_assist.services.postings_export import EXPORT_ROW_CAP, build_workbook_bytes
    from job_assist.services.postings_query import PostingsViewSpec, build_view_parts

    # Reuse the same validators the list endpoint uses; they raise 422
    # on bad input. per_company_cap is validated inline here (limit/
    # offset are not user-facing on this endpoint).
    if per_company_cap < 0:
        raise HTTPException(
            status_code=422,
            detail="per_company_cap must be >= 0 (0 disables the cap entirely)",
        )
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
        .limit(EXPORT_ROW_CAP)
    )
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
        PostingAction,
        PostingSource,
        TargetCompany,
    )
    from job_assist.services.posting_actions import latest_action_lateral

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
        "state": _state_block(pa_action_type, pa_reason, pa_snooze_until, pa_created_at),
        "description_markdown": jp.jd_text or None,
        "jd_summary_markdown": jp.jd_summary_markdown,
        "division": division_block,
        "posted_at": jp.posted_at.isoformat() if jp.posted_at else None,
        "last_seen_at": jp.last_seen_at.isoformat() if jp.last_seen_at else None,
        "closed_at": jp.closed_at.isoformat() if jp.closed_at else None,
        "state_history": state_history,
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
    from job_assist.services.posting_actions import record_action

    try:
        row = await record_action(
            db,
            posting_id,
            payload.action_type,
            payload.reason,
            payload.snooze_until,
            payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _state_block(row.action_type, row.reason, row.snooze_until, row.created_at)


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
            "stage": _enum_value(o.outcome_type),
            "confidence": o.classifier_confidence,
        }
        for o in rows
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


# ── Admin — cron status ────────────────────────────────────────────────────────


@app.get("/admin/cron-status")
async def cron_status() -> dict[str, str]:
    """Cron health-check endpoint.  Returns ok when the API is reachable."""
    return {"status": "ok"}


# ── Outcome event diagnostics (feat/admin-outcomes-stats) ────────────────────


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
