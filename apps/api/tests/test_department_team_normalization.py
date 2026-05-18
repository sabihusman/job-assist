"""Tests for the department/team normalization shim (PR #28a).

Sync tests cover the shared ``normalize_org_field`` helper and the
per-adapter extraction shapes; DB-gated tests exercise the migration,
the IngestionService upsert path, and the ``backfill_department_team``
service via the ``POST /admin/backfill/department-team`` endpoint.

No Gemini, no real ATS — every adapter test feeds a hand-crafted
``raw_payload`` to the existing ``GreenhouseAdapter.normalize`` /
``LeverAdapter.normalize`` / ``AshbyAdapter.normalize`` entrypoints
and asserts on the returned ``NormalizedPosting``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from job_assist.adapters.ashby import AshbyAdapter
from job_assist.adapters.base import RawPosting
from job_assist.adapters.greenhouse import GreenhouseAdapter
from job_assist.adapters.lever import LeverAdapter
from job_assist.adapters.normalization import normalize_org_field
from job_assist.db.models import (
    JobPosting,
    PostingSource,
    TargetCompany,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── normalize_org_field (sync) ────────────────────────────────────────────────


class TestNormalizeOrgField:
    def test_strips_whitespace(self) -> None:
        assert normalize_org_field("  Product  ") == "Product"

    def test_empty_string_becomes_none(self) -> None:
        assert normalize_org_field("") is None

    def test_whitespace_only_becomes_none(self) -> None:
        assert normalize_org_field("   ") is None

    def test_passes_through_normal_values(self) -> None:
        assert normalize_org_field("Engineering") == "Engineering"

    def test_preserves_case(self) -> None:
        assert normalize_org_field("DevOps") == "DevOps"

    def test_truncates_at_200_chars(self) -> None:
        very_long = "Eng" + "x" * 300  # well over 200
        result = normalize_org_field(very_long)
        assert result is not None
        assert len(result) == 200

    def test_none_passes_through(self) -> None:
        assert normalize_org_field(None) is None


# ── Greenhouse adapter (sync) ────────────────────────────────────────────────


def _greenhouse_payload(
    *, departments: list[dict[str, Any]] | None = None, missing_key: bool = False
) -> RawPosting:
    body: dict[str, Any] = {
        "id": 12345,
        "title": "Product Manager",
        "location": {"name": "Remote"},
        "content": "Description.",
    }
    if not missing_key:
        body["departments"] = departments if departments is not None else []
    return RawPosting(source_job_id="12345", raw_payload=body)


class TestGreenhouseAdapterExtraction:
    def _adapter(self) -> GreenhouseAdapter:
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            return_value=MagicMock(spec=httpx.Response, status_code=200, json=lambda: {})
        )
        return GreenhouseAdapter(client=mock_client)

    def test_extracts_department_from_departments_array(self) -> None:
        raw = _greenhouse_payload(departments=[{"id": 1, "name": "Engineering"}])
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department == "Engineering"
        assert norm.team is None  # Greenhouse doesn't expose a team concept

    def test_takes_first_when_multiple_departments(self) -> None:
        raw = _greenhouse_payload(
            departments=[
                {"id": 1, "name": "Engineering"},
                {"id": 2, "name": "Platform"},
            ]
        )
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department == "Engineering"

    def test_empty_departments_array_yields_none(self) -> None:
        raw = _greenhouse_payload(departments=[])
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department is None
        assert norm.team is None

    def test_missing_departments_key_yields_none(self) -> None:
        raw = _greenhouse_payload(missing_key=True)
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department is None

    def test_department_with_whitespace_is_normalised(self) -> None:
        raw = _greenhouse_payload(departments=[{"id": 1, "name": "  Engineering  "}])
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department == "Engineering"


# ── Lever adapter (sync) ──────────────────────────────────────────────────────


def _lever_payload(
    *, department: str | None = None, team: str | None = None, no_categories: bool = False
) -> RawPosting:
    body: dict[str, Any] = {
        "id": "lever-uuid",
        "text": "Senior Product Manager",
        "descriptionPlain": "x",
        "hostedUrl": "https://jobs.lever.co/test/lever-uuid",
        "createdAt": 1715000000000,
    }
    if not no_categories:
        body["categories"] = {"location": "Remote", "department": department, "team": team}
    return RawPosting(source_job_id="lever-uuid", raw_payload=body)


class TestLeverAdapterExtraction:
    def _adapter(self) -> LeverAdapter:
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            return_value=MagicMock(spec=httpx.Response, status_code=200, json=lambda: [])
        )
        return LeverAdapter(client=mock_client)

    def test_extracts_department_and_team(self) -> None:
        raw = _lever_payload(department="Product", team="Risk")
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department == "Product"
        assert norm.team == "Risk"

    def test_handles_both_null(self) -> None:
        raw = _lever_payload(department=None, team=None)
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department is None
        assert norm.team is None

    def test_handles_missing_categories(self) -> None:
        raw = _lever_payload(no_categories=True)
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department is None
        assert norm.team is None

    def test_strips_whitespace(self) -> None:
        raw = _lever_payload(department="  Product  ", team=" Risk ")
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department == "Product"
        assert norm.team == "Risk"


# ── Ashby adapter (sync) ──────────────────────────────────────────────────────


def _ashby_payload(*, department: str | None = None, team: str | None = None) -> RawPosting:
    return RawPosting(
        source_job_id="ashby-uuid",
        raw_payload={
            "id": "ashby-uuid",
            "title": "Senior Product Manager",
            "department": department,
            "team": team,
            "location": "Remote",
            "isRemote": True,
            "isListed": True,
            "isInternal": False,
            "publishedAt": "2026-05-01T12:00:00Z",
            "descriptionPlain": "x",
            "jobUrl": "https://jobs.ashbyhq.com/test/ashby-uuid",
        },
    )


class TestAshbyAdapterExtraction:
    def _adapter(self) -> AshbyAdapter:
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            return_value=MagicMock(spec=httpx.Response, status_code=200, json=lambda: {})
        )
        return AshbyAdapter(client=mock_client)

    def test_extracts_department_and_team(self) -> None:
        raw = _ashby_payload(department="Product", team="Growth")
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department == "Product"
        assert norm.team == "Growth"

    def test_handles_both_null(self) -> None:
        raw = _ashby_payload(department=None, team=None)
        norm = self._adapter().normalize(raw, canonical_company_name="TestCo")
        assert norm.department is None
        assert norm.team is None


# ── DB-gated: migration + upsert + backfill ──────────────────────────────────


@_NEEDS_DB
async def test_migration_adds_columns_and_indexes(db_session: Any) -> None:
    """The two new columns + two partial indexes exist after upgrade."""
    cols = (
        await db_session.execute(
            sa.text(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'job_posting'
                   AND column_name IN ('department', 'team')
                """
            )
        )
    ).all()
    by_name = {r.column_name: r for r in cols}
    assert set(by_name) == {"department", "team"}
    assert by_name["department"].is_nullable == "YES"
    assert by_name["team"].is_nullable == "YES"

    indexes = (
        await db_session.execute(
            sa.text(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE tablename = 'job_posting'
                   AND indexname IN (
                     'ix_job_posting_target_company_department',
                     'ix_job_posting_target_company_team'
                   )
                """
            )
        )
    ).all()
    names = {r.indexname for r in indexes}
    assert names == {
        "ix_job_posting_target_company_department",
        "ix_job_posting_target_company_team",
    }
    # Both indexes must be partial (WHERE …) — confirms the migration's
    # postgresql_where clause landed correctly.
    for row in indexes:
        assert "WHERE" in row.indexdef.upper()


def _make_job_posting(
    *,
    canonical_company_name: str = "TestCo",
    department: str | None = None,
    team: str | None = None,
) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name=canonical_company_name,
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        jd_text="x",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        department=department,
        team=team,
    )


def _make_posting_source(
    *,
    job_posting_id: uuid.UUID,
    ats: str,
    raw_payload: dict[str, Any],
    fetched_at: datetime | None = None,
) -> PostingSource:
    return PostingSource(
        job_posting_id=job_posting_id,
        ats=ats,
        source_job_id=uuid.uuid4().hex,
        source_url="https://example.com/job",
        apply_url=None,
        raw_payload=raw_payload,
        parser_version="test-v1",
        fetch_status="ok",
        fetched_at=fetched_at or datetime.now(tz=UTC),
    )


@_NEEDS_DB
async def test_upsert_writes_department_team(db_session: Any) -> None:
    """A new JobPosting saved with both fields round-trips correctly."""
    posting = _make_job_posting(department="Engineering", team="Platform")
    db_session.add(posting)
    await db_session.commit()

    fetched = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting.id))
    ).scalar_one()
    assert fetched.department == "Engineering"
    assert fetched.team == "Platform"


# ── Backfill tests (per-ATS extraction) ──────────────────────────────────────


@_NEEDS_DB
async def test_backfill_extracts_from_greenhouse_raw_payload(db_session: Any) -> None:
    from job_assist.services.posting_backfill import backfill_department_team

    posting = _make_job_posting()
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="greenhouse",
            raw_payload={
                "id": 555,
                "title": "Senior PM",
                "departments": [{"id": 7, "name": "Engineering"}],
            },
        )
    )
    await db_session.commit()

    report = await backfill_department_team(db_session)
    assert report.candidates >= 1
    assert report.updated >= 1

    await db_session.refresh(posting)
    assert posting.department == "Engineering"
    assert posting.team is None


@_NEEDS_DB
async def test_backfill_extracts_from_lever_raw_payload(db_session: Any) -> None:
    from job_assist.services.posting_backfill import backfill_department_team

    posting = _make_job_posting()
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="lever",
            raw_payload={
                "id": "lever-x",
                "text": "Senior PM",
                "categories": {"department": "Product", "team": "Risk"},
            },
        )
    )
    await db_session.commit()

    await backfill_department_team(db_session)
    await db_session.refresh(posting)
    assert posting.department == "Product"
    assert posting.team == "Risk"


@_NEEDS_DB
async def test_backfill_extracts_from_ashby_raw_payload(db_session: Any) -> None:
    from job_assist.services.posting_backfill import backfill_department_team

    posting = _make_job_posting()
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="ashby",
            raw_payload={
                "id": "ashby-x",
                "title": "Senior PM",
                "department": "Product",
                "team": "Growth",
            },
        )
    )
    await db_session.commit()

    await backfill_department_team(db_session)
    await db_session.refresh(posting)
    assert posting.department == "Product"
    assert posting.team == "Growth"


@_NEEDS_DB
async def test_backfill_skips_already_populated_rows(db_session: Any) -> None:
    """A row with department already set must NOT be re-written."""
    from job_assist.services.posting_backfill import backfill_department_team

    posting = _make_job_posting(department="Operator-Set")
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="greenhouse",
            raw_payload={"departments": [{"name": "Should Not Win"}]},
        )
    )
    await db_session.commit()

    report = await backfill_department_team(db_session)
    # The pre-populated row should not be in the candidate set at all.
    await db_session.refresh(posting)
    assert posting.department == "Operator-Set"
    # candidates count: only NULL-NULL rows. The pre-populated one isn't here.
    assert report.candidates == 0


@_NEEDS_DB
async def test_backfill_handles_missing_payload_fields(db_session: Any) -> None:
    from job_assist.services.posting_backfill import backfill_department_team

    posting = _make_job_posting()
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="greenhouse",
            raw_payload={"id": 1, "title": "PM"},  # no departments key
        )
    )
    await db_session.commit()

    report = await backfill_department_team(db_session)
    await db_session.refresh(posting)
    assert posting.department is None
    assert posting.team is None
    assert report.skipped_no_data >= 1


@_NEEDS_DB
async def test_backfill_prefers_most_recent_posting_source(db_session: Any) -> None:
    """When a posting has two sources, the latest ``fetched_at`` wins."""
    from job_assist.services.posting_backfill import backfill_department_team

    posting = _make_job_posting()
    db_session.add(posting)
    await db_session.flush()

    older = datetime(2026, 5, 1, tzinfo=UTC)
    newer = datetime(2026, 5, 10, tzinfo=UTC)
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="greenhouse",
            raw_payload={"departments": [{"name": "OldDept"}]},
            fetched_at=older,
        )
    )
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="lever",
            raw_payload={"categories": {"department": "NewDept", "team": "NewTeam"}},
            fetched_at=newer,
        )
    )
    await db_session.commit()

    await backfill_department_team(db_session)
    await db_session.refresh(posting)
    assert posting.department == "NewDept"
    assert posting.team == "NewTeam"


# ── Endpoint smoke ───────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_backfill_endpoint_returns_counts(db_session: Any) -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    posting = _make_job_posting()
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        _make_posting_source(
            job_posting_id=posting.id,
            ats="greenhouse",
            raw_payload={"departments": [{"name": "Engineering"}]},
        )
    )
    await db_session.commit()

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/admin/backfill/department-team")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"candidates", "updated", "skipped_no_source", "skipped_no_data"}
    assert body["candidates"] >= 1
    assert body["updated"] >= 1


# ── Ingestion-path coverage (self-heal on re-ingest) ─────────────────────────


@_NEEDS_DB
async def test_ingestion_self_heal_on_reingest(db_session: Any) -> None:
    """A NULL department gets filled when the same posting re-ingests with a value.

    Mirrors the operator's daily-cron experience after this PR lands:
    yesterday's row was created without department/team; today's run sees
    the same content_hash and the upsert path fills the new columns.
    """
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    # Pre-create the row in the "old" state (no dept/team).
    tc = TargetCompany(
        name="HealCo",
        tier=1,
        ats="greenhouse",
        ats_handle="healco",
    )
    db_session.add(tc)
    await db_session.flush()

    existing = JobPosting(
        canonical_company_name="HealCo",
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        jd_text="old",
        jd_text_hash="0" * 64,
        # Same content_hash the Greenhouse adapter will compute below.
        content_hash="will-overwrite",
        first_seen_at=datetime.now(tz=UTC),
        last_seen_at=datetime.now(tz=UTC),
        department=None,
        team=None,
        target_company_id=tc.id,
    )
    db_session.add(existing)
    await db_session.flush()

    # Greenhouse adapter wired with a mock client returning the synthetic job.
    payload = {
        "id": 12345,
        "title": "Senior Product Manager",
        "location": {"name": "Remote"},
        "content": "Description.",
        "absolute_url": "https://example.com/job/1",
        "departments": [{"id": 1, "name": "Engineering"}],
        "first_published": "2026-05-01T00:00:00Z",
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"jobs": [payload]}
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    adapter = GreenhouseAdapter(client=mock_client)

    # Align the existing row's content_hash with what the adapter will compute,
    # so the IngestionService takes the UPDATE branch (not INSERT).
    raw = RawPosting(source_job_id="12345", raw_payload=payload)
    norm = adapter.normalize(raw, canonical_company_name="HealCo")
    existing.content_hash = norm.content_hash
    await db_session.commit()

    from job_assist.services.ingestion import IngestionService

    await IngestionService().ingest_source(adapter, "healco", db_session)

    await db_session.refresh(existing)
    # Self-heal: the Greenhouse adapter put "Engineering" on the NormalizedPosting,
    # and the UPDATE branch in IngestionService filled the NULL column.
    assert existing.department == "Engineering"
