"""Integration tests for IngestionService counter accuracy.

Production observation that motivated these tests
─────────────────────────────────────────────────
A first run against Stripe inserted 487 job_posting rows but the
IngestRun reported postings_new=0, postings_updated=487. The counters
were inverted relative to the actual DB writes.

These tests pin the contract:
  * Empty table  → every posting counts as new, none as updated.
  * Re-ingest    → every posting counts as updated, none as new.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select

from job_assist.adapters.greenhouse import GreenhouseAdapter
from job_assist.db.models.job_posting import JobPosting
from job_assist.services.ingestion import IngestionService

_FIXTURE_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "greenhouse_stripe.json"
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _make_adapter(jobs: list[dict[str, Any]] | None = None) -> GreenhouseAdapter:
    """Adapter wired to a mock httpx client serving the local fixture."""
    payload = {"jobs": jobs if jobs is not None else _FIXTURE["jobs"]}
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return GreenhouseAdapter(client=mock_client)


@_NEEDS_DB
async def test_counters_new_vs_updated(db_session: Any) -> None:
    """First run → all new; second run with identical data → all updated."""
    expected = len(_FIXTURE["jobs"])
    service = IngestionService()

    # Sanity: table starts empty (the db_session fixture truncates between tests).
    starting_count: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert starting_count == 0, "Test precondition: job_posting must be empty"

    # ── First run: every posting is new ──────────────────────────────────────
    run1 = await service.ingest_source(_make_adapter(), "stripe", db_session)
    assert run1.status == "success"
    assert run1.postings_fetched == expected
    assert run1.postings_new == expected, (
        f"First run should count {expected} new postings; got {run1.postings_new}"
    )
    assert run1.postings_updated == 0, (
        f"First run should count 0 updated postings; got {run1.postings_updated}"
    )

    rows_after_first: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert rows_after_first == expected, (
        "DB should contain exactly one row per fixture job after the first run"
    )

    # ── Second run with identical data: every posting is an update ──────────
    run2 = await service.ingest_source(_make_adapter(), "stripe", db_session)
    assert run2.status == "success"
    assert run2.postings_fetched == expected
    assert run2.postings_new == 0, (
        f"Second run should count 0 new postings; got {run2.postings_new}"
    )
    assert run2.postings_updated == expected, (
        f"Second run should count {expected} updated postings; got {run2.postings_updated}"
    )

    rows_after_second: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert rows_after_second == expected, (
        "Idempotency: row count must not change on re-ingest of identical data"
    )


# ── HandleNotFoundError catch (Bestiary 5.9) ──────────────────────────────────


@_NEEDS_DB
async def test_handle_not_found_is_recorded_as_distinct_status(db_session: Any) -> None:
    """A HandleNotFoundError from any adapter is caught by the
    orchestrator and recorded as ``IngestRun.status='handle_not_found'``
    — distinct from generic ``failed``. The run completes cleanly with
    zero postings_fetched and no traceback.

    Bestiary 5.9: lets the operator distinguish a stale ATS handle from
    a transient network failure when scanning recent runs.
    """
    from job_assist.adapters.base import HandleNotFoundError
    from job_assist.db.enums import IngestRunStatus

    # Adapter stub that raises HandleNotFoundError on fetch_postings.
    class _StubAdapter:
        ats = "lever"
        parser_version = "stub-v1"

        async def fetch_postings(self, handle: str) -> list[Any]:
            raise HandleNotFoundError(
                ats=self.ats,
                handle=handle,
                url=f"https://api.lever.co/v0/postings/{handle}?mode=json",
            )

        def normalize(self, raw: Any, canonical_company_name: str) -> Any:
            raise AssertionError("normalize should never be called when fetch raises")

    service = IngestionService()
    run = await service.ingest_source(_StubAdapter(), "ghost-handle", db_session)

    assert run.status == IngestRunStatus.handle_not_found.value
    assert run.postings_fetched == 0
    assert run.postings_new == 0
    assert run.postings_updated == 0
    assert run.error_traceback is None  # not a stack-worthy failure
    assert "ghost-handle" in (run.error_message or "")


# ── last_swept_at stamping (fix/audit per-pipeline health) ────────────────────


@_NEEDS_DB
async def test_ingest_source_stamps_last_swept_at_on_success(db_session: Any) -> None:
    """A successful run stamps the resolved company's last_swept_at — the
    curated_fresh health check reads MAX(last_swept_at WHERE source='curated'),
    so the free-adapter daily cron must leave the same footprint the Apify
    path does."""
    from job_assist.db.models.target_company import TargetCompany

    tc = TargetCompany(
        name="StampCo",
        tier=1,
        ats="greenhouse",  # type: ignore[arg-type]
        ats_handle="stripe",
        domain="stampco.com",
        source="curated",
        last_swept_at=None,
    )
    db_session.add(tc)
    await db_session.commit()

    service = IngestionService()
    run = await service.ingest_source(_make_adapter(), "stripe", db_session)
    assert run.status == "success"

    await db_session.refresh(tc)
    assert tc.last_swept_at is not None


@_NEEDS_DB
async def test_ingest_source_does_not_stamp_last_swept_at_on_failure(db_session: Any) -> None:
    """A FAILED run must NOT stamp last_swept_at — otherwise a dead/erroring
    sweep keeps the curated_fresh alarm green (same contract PR #196 pinned
    for the Apify path)."""
    from job_assist.db.models.target_company import TargetCompany

    class _BoomAdapter:
        ats = "greenhouse"
        parser_version = "stub-v1"

        async def fetch_postings(self, handle: str) -> list[Any]:
            raise RuntimeError("simulated board failure")

        def normalize(self, raw: Any, canonical_company_name: str) -> Any:
            raise AssertionError("normalize should never be called when fetch raises")

    tc = TargetCompany(
        name="BoomCo",
        tier=1,
        ats="greenhouse",  # type: ignore[arg-type]
        ats_handle="boomco",
        domain="boomco.com",
        source="curated",
        last_swept_at=None,
    )
    db_session.add(tc)
    await db_session.commit()

    service = IngestionService()
    run = await service.ingest_source(_BoomAdapter(), "boomco", db_session)  # type: ignore[arg-type]
    assert run.status == "failed"

    await db_session.refresh(tc)
    assert tc.last_swept_at is None


# ── Salary self-heal on re-ingest (Greenhouse salary fix) ─────────────────────


def _gh_job_with_content(content: str) -> dict[str, Any]:
    """A single fixed Greenhouse job, varying only the content body.

    content_hash derives from (name, title, locations) — all constant here —
    so two payloads with different ``content`` resolve to the SAME posting
    and exercise the update branch.
    """
    return {
        "id": 555001,
        "title": "Senior Product Manager",
        "location": {"name": "Remote"},
        "absolute_url": "https://example.test/jobs/555001",
        "content": content,
        "first_published": "2026-05-01T00:00:00Z",
        "departments": [],
    }


@_NEEDS_DB
async def test_salary_self_heals_on_reingest(db_session: Any) -> None:
    """Existing row with NULL salary + re-ingest where the JD body now
    carries a pay range → self-heal backfills salary_min/max/currency.
    Mirrors the department/team self-heal contract.
    """
    service = IngestionService()
    no_pay = "&lt;p&gt;We are hiring a Senior PM. Comp not listed.&lt;/p&gt;"
    with_pay = (
        "&lt;p&gt;We are hiring a Senior PM. Base pay "
        "$190,000&lt;span&gt;&amp;mdash;&lt;/span&gt;$240,000 USD.&lt;/p&gt;"
    )

    # First ingest: no pay in body → salary NULL.
    await service.ingest_source(_make_adapter([_gh_job_with_content(no_pay)]), "stripe", db_session)
    row = (await db_session.execute(select(JobPosting))).scalars().one()
    assert row.salary_min is None and row.salary_max is None

    # Re-ingest same posting, now with a pay range → self-heal fills it.
    await service.ingest_source(
        _make_adapter([_gh_job_with_content(with_pay)]), "stripe", db_session
    )
    await db_session.refresh(row)
    assert row.salary_min == 190000
    assert row.salary_max == 240000
    assert row.salary_currency == "USD"


@_NEEDS_DB
async def test_salary_self_heal_never_overwrites_existing(db_session: Any) -> None:
    """Existing row with a NON-NULL salary must NOT be overwritten on
    re-ingest, even if the new payload parses a different range — fill-if-
    NULL only, same guard as department/team.
    """
    service = IngestionService()
    pay_a = "&lt;p&gt;Base pay $190,000&lt;span&gt;&amp;mdash;&lt;/span&gt;$240,000 USD.&lt;/p&gt;"
    pay_b = "&lt;p&gt;Base pay $300,000&lt;span&gt;&amp;mdash;&lt;/span&gt;$400,000 USD.&lt;/p&gt;"

    await service.ingest_source(_make_adapter([_gh_job_with_content(pay_a)]), "stripe", db_session)
    row = (await db_session.execute(select(JobPosting))).scalars().one()
    assert row.salary_min == 190000  # set on first ingest

    # Re-ingest with a different range — must be ignored (fill-if-NULL only).
    await service.ingest_source(_make_adapter([_gh_job_with_content(pay_b)]), "stripe", db_session)
    await db_session.refresh(row)
    assert row.salary_min == 190000, "self-heal must not overwrite an existing salary"
    assert row.salary_max == 240000


# ── Hard-rule eligibility wiring (PR C) ───────────────────────────────────────


async def _set_operator_profile(db_session: Any, **fields: Any) -> None:
    """Upsert the singleton operator_profile (id=1) with the given rule fields.

    The test DB is already seeded with the singleton row (seed migration), so
    INSERTing id=1 hits the PK. Fetch-and-mutate when it exists; insert only
    as a local-edge fallback. Every rule-relevant field is overwritten so the
    test is deterministic regardless of the seeded defaults.
    """
    from job_assist.db.models.operator_profile import OperatorProfile

    existing = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(OperatorProfile(id=1, looking_for_text="PM", role_keywords=[], **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
    await db_session.commit()


@_NEEDS_DB
async def test_ingest_persists_hard_rule_failed(db_session: Any) -> None:
    """Ingest evaluates apply_hard_rules and stores the failed RuleName.

    Seeds the operator_profile with a staffing-firm blocklist matching the
    derived canonical company name ("Stripe", from the 'stripe' handle with
    no target_company row) so every inserted posting fails the staffing_firm
    rule deterministically — independent of salary text-mining."""
    await _set_operator_profile(
        db_session,
        geo_whitelist=["Remote"],
        salary_floor_usd=1,
        salary_ceiling_usd=None,
        applicant_cap=500,
        seniority_levels_included=None,
        staffing_firm_blocklist=["Stripe"],
    )

    service = IngestionService()
    run = await service.ingest_source(_make_adapter(), "stripe", db_session)
    assert run.status == "success"

    rows = (await db_session.execute(select(JobPosting))).scalars().all()
    assert rows, "fixture should insert at least one posting"
    for row in rows:
        assert row.hard_rule_failed == "staffing_firm"
        assert row.hard_rules_evaluated_at is not None


@_NEEDS_DB
async def test_ingest_passes_hard_rules_when_nothing_fails(db_session: Any) -> None:
    """A seeded profile with permissive rules leaves hard_rule_failed NULL,
    but hard_rules_evaluated_at is still stamped (the eval ran)."""
    await _set_operator_profile(
        db_session,
        geo_whitelist=["Remote", "San Francisco", "New York", "Remote - US"],
        salary_floor_usd=1,
        salary_ceiling_usd=None,
        applicant_cap=10_000,
        seniority_levels_included=None,
        staffing_firm_blocklist=[],
    )

    service = IngestionService()
    await service.ingest_source(
        _make_adapter([_gh_job_with_content("<p>PM role.</p>")]), "stripe", db_session
    )
    row = (await db_session.execute(select(JobPosting))).scalars().one()
    assert row.hard_rule_failed is None
    assert row.hard_rules_evaluated_at is not None


# ── Stale-posting detection (Bestiary 5.18) ───────────────────────────────────

import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from job_assist.services.ingestion import mark_stale_postings  # noqa: E402


def _bare_posting(*, last_seen_days_ago: int, closed: bool = False) -> JobPosting:
    """A minimal JobPosting with a controllable last_seen_at age.

    last_seen_at = first_seen_at = now - last_seen_days_ago. (last_seen_at is
    NOT NULL on the model — there is no NULL case to handle; the mark-stale
    query compares it against a cutoff, never against NULL.)
    """
    now = datetime.now(tz=UTC)
    seen = now - timedelta(days=last_seen_days_ago)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="StaleCo",
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        remote_type="remote",
        role_family="product_management",
        seniority_level="senior_pm",
        jd_text="JD body.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=seen,
        last_seen_at=seen,
        closed_at=(now if closed else None),
    )


@_NEEDS_DB
async def test_mark_stale_postings(db_session: Any) -> None:
    """8d-stale open posting → closed; 2d-recent → stays open; already-closed
    → not re-stamped."""
    old = _bare_posting(last_seen_days_ago=8)
    recent = _bare_posting(last_seen_days_ago=2)
    already = _bare_posting(last_seen_days_ago=30, closed=True)
    already_closed_at = already.closed_at
    db_session.add_all([old, recent, already])
    await db_session.commit()

    marked = await mark_stale_postings(db_session, stale_after_days=7)
    assert marked == 1, f"only the 8d-stale open posting should be marked; got {marked}"

    await db_session.refresh(old)
    await db_session.refresh(recent)
    await db_session.refresh(already)
    assert old.closed_at is not None, "8d-stale posting must be closed"
    assert recent.closed_at is None, "2d-recent posting must stay open"
    assert already.closed_at == already_closed_at, "already-closed must not be re-stamped"


@_NEEDS_DB
async def test_reappearance_clears_closed_at(db_session: Any) -> None:
    """A closed posting that reappears on the ATS (re-ingested with a fresh
    sighting) must be re-opened — closed_at cleared."""
    service = IngestionService()
    content = "&lt;p&gt;Senior PM role.&lt;/p&gt;"

    # First ingest creates the row.
    await service.ingest_source(
        _make_adapter([_gh_job_with_content(content)]), "stripe", db_session
    )
    row = (await db_session.execute(select(JobPosting))).scalars().one()

    # Simulate the posting having been marked stale.
    row.closed_at = datetime.now(tz=UTC) - timedelta(days=10)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.closed_at is not None

    # Re-ingest the same posting (still on the board) → reappearance re-opens it.
    await service.ingest_source(
        _make_adapter([_gh_job_with_content(content)]), "stripe", db_session
    )
    await db_session.refresh(row)
    assert row.closed_at is None, "reappeared posting must have closed_at cleared"


# ── fix/ingest-lifecycle (audit HIGH #2): warm-path cohort mark-stale window ──


@_NEEDS_DB
async def test_warm_path_postings_get_longer_stale_window(db_session: Any) -> None:
    """A warm-path posting (matched company source='warm_path') aged 8 days —
    past the 7d daily cutoff but inside the 10d warm window — must NOT close,
    because that cohort is swept only weekly. An unmatched posting (the bulk,
    target_company_id NULL) and a curated-company posting at 8d DO close. A
    warm-path posting past the 10d window closes."""
    from job_assist.db.models import TargetCompany

    warm_co = TargetCompany(name="WarmDeere", ats="workday", domain="d.com", source="warm_path")
    curated_co = TargetCompany(name="CuratedCo", tier=1, ats="greenhouse", source="curated")
    db_session.add_all([warm_co, curated_co])
    await db_session.flush()

    warm_8d = _bare_posting(last_seen_days_ago=8)
    warm_8d.target_company_id = warm_co.id
    warm_12d = _bare_posting(last_seen_days_ago=12)
    warm_12d.target_company_id = warm_co.id
    curated_8d = _bare_posting(last_seen_days_ago=8)
    curated_8d.target_company_id = curated_co.id
    unmatched_8d = _bare_posting(last_seen_days_ago=8)  # target_company_id NULL
    db_session.add_all([warm_8d, warm_12d, curated_8d, unmatched_8d])
    await db_session.commit()

    await mark_stale_postings(db_session, stale_after_days=7)
    for p in (warm_8d, warm_12d, curated_8d, unmatched_8d):
        await db_session.refresh(p)

    assert warm_8d.closed_at is None, "warm-path 8d posting must survive the daily cutoff"
    assert warm_12d.closed_at is not None, "warm-path 12d posting (past 10d) must close"
    assert curated_8d.closed_at is not None, "curated 8d posting closes on the daily cutoff"
    assert unmatched_8d.closed_at is not None, "unmatched (NULL company) 8d posting closes daily"


# ── fix/ingest-lifecycle (audit HIGH #7): PostingSource re-point on retitle ──


def _gh_job(*, job_id: int, title: str, url: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "location": {"name": "Remote"},
        "absolute_url": url,
        "content": "<p>PM role.</p>",
        "first_published": "2026-05-01T00:00:00Z",
        "departments": [],
    }


@_NEEDS_DB
async def test_posting_source_repoints_when_content_hash_changes(db_session: Any) -> None:
    """An ATS edit to a live posting's title changes content_hash → a NEW
    JobPosting is inserted, but the SAME (ats, source_job_id) PostingSource must
    RE-POINT to it (not stay orphaned on the old posting). Otherwise the new
    posting has no apply URL and is invisible to the ats filter."""
    from sqlalchemy import select as sa_select

    from job_assist.db.models import PostingSource

    service = IngestionService()

    # First sight: title A.
    await service.ingest_source(
        _make_adapter([_gh_job(job_id=900001, title="Product Manager", url="https://x.test/a")]),
        "stripe",
        db_session,
    )
    # Same source id, EDITED title → new content_hash → new JobPosting.
    await service.ingest_source(
        _make_adapter(
            [_gh_job(job_id=900001, title="Staff Product Manager", url="https://x.test/b")]
        ),
        "stripe",
        db_session,
    )

    # Exactly one PostingSource for this source id (upsert by ats+source_job_id).
    sources = (
        (
            await db_session.execute(
                sa_select(PostingSource).where(PostingSource.source_job_id == "900001")
            )
        )
        .scalars()
        .all()
    )
    assert len(sources) == 1, "one PostingSource per (ats, source_job_id)"
    src = sources[0]

    # It must point at the NEW (latest-title) JobPosting, with the fresh URL.
    new_posting = (
        await db_session.execute(
            sa_select(JobPosting).where(JobPosting.raw_title == "Staff Product Manager")
        )
    ).scalar_one()
    assert src.job_posting_id == new_posting.id, "source must re-point to the retitled posting"
    assert src.source_url == "https://x.test/b", "provenance URL refreshed to the new posting"
