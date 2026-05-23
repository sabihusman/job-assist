"""Tests for ``GET /postings?sort=...`` (PR #49).

Sync tests (no DB) cover Pydantic-level validation: the ``sort`` query
param is a ``Literal`` so FastAPI 422's on unknown values without ever
reaching the handler.

DB-gated tests assert per-sort ordering on a deterministic fixture of
6 postings with mixed NULL/non-NULL salary_max + tier + posted_at, and
re-check the 2-query budget for each variant.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import JobPosting, PostingSource, TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Client + execute counter (mirror of test_read_endpoints.py) ──────────────


async def _client(db_session: Any) -> AsyncClient:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drop_override() -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    app.dependency_overrides.pop(get_db, None)


class _ExecuteCounter:
    """Wraps ``session.execute`` to count statements emitted by an endpoint."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._original = session.execute
        self.count = 0

    async def _wrapped(self, *args: Any, **kwargs: Any) -> Any:
        self.count += 1
        return await self._original(*args, **kwargs)

    def __enter__(self) -> _ExecuteCounter:
        self._session.execute = self._wrapped  # type: ignore[method-assign]
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._session.execute = self._original  # type: ignore[method-assign]

    async def __aenter__(self) -> _ExecuteCounter:
        return self.__enter__()

    async def __aexit__(self, *_exc: Any) -> None:
        self.__exit__(*_exc)


# ── Validation (no DB) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_sort_returns_422() -> None:
    """Unknown sort key → 422 at the FastAPI Literal validator.

    This is the only validation test we need: FastAPI's Pydantic Literal
    accepts iff the value is one of the union members. If the validator
    accepts a value, it WILL reach the handler — that's by definition.
    A parametrized "valid keys reach handler" test would just re-prove
    Pydantic's correctness while requiring a real DB to complete the
    handler's COUNT query.
    """
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/postings?sort=salary_low_to_high")
    assert resp.status_code == 422


def test_sort_key_literal_membership() -> None:
    """Schema-level check that all 5 documented sort keys are in the
    SortKey Literal. Catches accidental enum drift between
    schemas/public.py and the SortDropdown frontend component."""
    from typing import get_args

    from job_assist.schemas.public import DEFAULT_SORT, SortKey

    members = set(get_args(SortKey))
    assert members == {
        "newest",
        "oldest",
        "salary_high_to_low",
        "tier",
        "recently_posted",
    }
    assert DEFAULT_SORT == "newest"


# ── Fixture builders ─────────────────────────────────────────────────────────


def _company(name: str, tier: int) -> TargetCompany:
    return TargetCompany(
        name=name,
        tier=tier,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(
    *,
    target_company_id: uuid.UUID | None,
    first_seen_at: datetime,
    posted_at: datetime | None = None,
    salary_max: int | None = None,
    title: str = "Senior Product Manager",
) -> JobPosting:
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=title.lower(),
        raw_title=title,
        jd_text="JD body.",
        jd_text_hash=f"{'0' * 54}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        posted_at=posted_at,
        salary_max=salary_max,
    )


async def _seed_sort_fixture(db_session: Any) -> dict[str, uuid.UUID]:
    """Six postings + four companies designed to disambiguate every sort.

    Layout (id → tier / first_seen_at / posted_at / salary_max):

      p_t1_new   → tier=1, first_seen=now,        posted=now-2h,  salary=300_000
      p_t2_mid   → tier=2, first_seen=now-1d,     posted=now-1d,  salary=200_000
      p_t3_old   → tier=3, first_seen=now-7d,     posted=now-7d,  salary=NULL
      p_t4_new   → tier=4, first_seen=now-5m,     posted=NULL,    salary=150_000
      p_no_tc    → no company,  first_seen=now-2d, posted=now-3d, salary=400_000
      p_no_dates → tier=2, first_seen=now-3d,     posted=NULL,    salary=NULL
    """
    now = datetime.now(tz=UTC)

    c1, c2, c3, c4 = (
        _company("T1Co", 1),
        _company("T2Co", 2),
        _company("T3Co", 3),
        _company("T4Co", 4),
    )
    db_session.add_all([c1, c2, c3, c4])
    await db_session.flush()

    postings = {
        "p_t1_new": _posting(
            target_company_id=c1.id,
            first_seen_at=now,
            posted_at=now - timedelta(hours=2),
            salary_max=300_000,
        ),
        "p_t2_mid": _posting(
            target_company_id=c2.id,
            first_seen_at=now - timedelta(days=1),
            posted_at=now - timedelta(days=1),
            salary_max=200_000,
        ),
        "p_t3_old": _posting(
            target_company_id=c3.id,
            first_seen_at=now - timedelta(days=7),
            posted_at=now - timedelta(days=7),
            salary_max=None,
        ),
        "p_t4_new": _posting(
            target_company_id=c4.id,
            first_seen_at=now - timedelta(minutes=5),
            posted_at=None,
            salary_max=150_000,
        ),
        "p_no_tc": _posting(
            target_company_id=None,
            first_seen_at=now - timedelta(days=2),
            posted_at=now - timedelta(days=3),
            salary_max=400_000,
        ),
        "p_no_dates": _posting(
            target_company_id=c2.id,
            first_seen_at=now - timedelta(days=3),
            posted_at=None,
            salary_max=None,
        ),
    }
    for jp in postings.values():
        db_session.add(jp)
    await db_session.flush()
    # Every posting needs at least one posting_source for realism (the
    # endpoint LATERAL-joins to it but a missing row still produces a
    # NULL ats — we attach one anyway so future asserts on `source` work).
    # Mirror the full PostingSource factory shape from
    # ``tests/test_read_endpoints.py::_posting_source``. Several columns
    # on posting_source are NOT NULL (source_job_id, raw_payload,
    # parser_version, fetch_status); skipping any of them trips an
    # asyncpg NotNullViolationError on the flush.
    for jp in postings.values():
        db_session.add(
            PostingSource(
                job_posting_id=jp.id,
                ats="greenhouse",
                source_job_id=uuid.uuid4().hex,
                source_url=f"https://example.test/{jp.id}",
                apply_url=None,
                raw_payload={},
                parser_version="test-v1",
                fetch_status="ok",
                fetched_at=datetime.now(tz=UTC),
            )
        )
    await db_session.commit()
    return {k: v.id for k, v in postings.items()}


async def _fetch_ids(ac: AsyncClient, sort: str | None = None) -> list[str]:
    """GET /postings and return the items' ids in the order returned.

    Disable the default ``state=triage`` filter handling by NOT passing
    ``state`` — the endpoint then includes every posting regardless of
    operator action.
    """
    qs = f"?sort={sort}&limit=100" if sort else "?limit=100"
    resp = await ac.get(f"/postings{qs}")
    assert resp.status_code == 200, resp.text
    return [item["id"] for item in resp.json()["items"]]


# ── Ordering (DB-gated) ──────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sort_default_equals_newest(db_session: Any) -> None:
    """No ``?sort=`` produces the same order as ``?sort=newest``."""
    ids = await _seed_sort_fixture(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            default_order = await _fetch_ids(ac)
            newest_order = await _fetch_ids(ac, "newest")
    finally:
        await _drop_override()

    assert default_order == newest_order
    # Sanity: t1_new is the most-recent first_seen_at.
    assert default_order[0] == str(ids["p_t1_new"])


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sort_newest_descends_by_first_seen_at(db_session: Any) -> None:
    ids = await _seed_sort_fixture(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            order = await _fetch_ids(ac, "newest")
    finally:
        await _drop_override()

    expected = [
        ids["p_t1_new"],  # now
        ids["p_t4_new"],  # now - 5m
        ids["p_t2_mid"],  # now - 1d
        ids["p_no_tc"],  # now - 2d
        ids["p_no_dates"],  # now - 3d
        ids["p_t3_old"],  # now - 7d
    ]
    assert order == [str(x) for x in expected]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sort_oldest_ascends_by_first_seen_at(db_session: Any) -> None:
    ids = await _seed_sort_fixture(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            order = await _fetch_ids(ac, "oldest")
    finally:
        await _drop_override()

    expected = [
        ids["p_t3_old"],
        ids["p_no_dates"],
        ids["p_no_tc"],
        ids["p_t2_mid"],
        ids["p_t4_new"],
        ids["p_t1_new"],
    ]
    assert order == [str(x) for x in expected]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sort_salary_high_to_low_nulls_last(db_session: Any) -> None:
    """salary_max descending; NULL salaries at the bottom."""
    ids = await _seed_sort_fixture(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            order = await _fetch_ids(ac, "salary_high_to_low")
    finally:
        await _drop_override()

    # Non-NULL salary rows, descending: 400k, 300k, 200k, 150k.
    non_null = [
        ids["p_no_tc"],  # 400_000
        ids["p_t1_new"],  # 300_000
        ids["p_t2_mid"],  # 200_000
        ids["p_t4_new"],  # 150_000
    ]
    # The two NULL-salary rows trail, sorted by id ASC tiebreaker. We
    # don't pin their internal order (id is random uuids), just that
    # they're both at the tail.
    null_rows = {str(ids["p_t3_old"]), str(ids["p_no_dates"])}
    assert order[:4] == [str(x) for x in non_null]
    assert set(order[4:]) == null_rows


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sort_tier_ascending_nulls_last(db_session: Any) -> None:
    """Tier ascending (T1 = best); postings without a target_company at the bottom."""
    ids = await _seed_sort_fixture(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            order = await _fetch_ids(ac, "tier")
    finally:
        await _drop_override()

    # T1, T2 (two rows), T3, T4 — then the no-company posting last.
    # Two T2 rows tiebreak by id ASC, so just assert membership of the
    # T2 slot, not order.
    assert order[0] == str(ids["p_t1_new"])
    assert order[3] == str(ids["p_t3_old"])
    assert order[4] == str(ids["p_t4_new"])
    assert order[5] == str(ids["p_no_tc"])
    # Slots 1 and 2 are the two T2 rows in some id-determined order.
    assert {order[1], order[2]} == {str(ids["p_t2_mid"]), str(ids["p_no_dates"])}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sort_recently_posted_nulls_last(db_session: Any) -> None:
    """posted_at descending; NULL posted_at rows at the bottom."""
    ids = await _seed_sort_fixture(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            order = await _fetch_ids(ac, "recently_posted")
    finally:
        await _drop_override()

    non_null = [
        ids["p_t1_new"],  # now - 2h
        ids["p_t2_mid"],  # now - 1d
        ids["p_no_tc"],  # now - 3d
        ids["p_t3_old"],  # now - 7d
    ]
    null_rows = {str(ids["p_t4_new"]), str(ids["p_no_dates"])}
    assert order[:4] == [str(x) for x in non_null]
    assert set(order[4:]) == null_rows


# ── Query budget — sort must not add a third query ──────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sort_key",
    ["newest", "oldest", "salary_high_to_low", "tier", "recently_posted"],
)
async def test_sort_preserves_two_query_budget(db_session: Any, sort_key: str) -> None:
    """Every sort key still emits ≤2 SQL statements (COUNT + SELECT)."""
    await _seed_sort_fixture(db_session)
    counter = _ExecuteCounter(db_session)
    ac = await _client(db_session)
    try:
        async with ac, counter:
            resp = await ac.get(f"/postings?sort={sort_key}&limit=100")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert counter.count <= 2, f"sort={sort_key} emitted {counter.count} queries (expected ≤2)"
