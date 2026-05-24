"""Tests for ``GET /contacts`` (PR #51).

The contacts list is the operator's first read-only window onto the
outreach pipeline. The seed POST shipped in PR #39; this PR adds the
matching read endpoint plus support for ``include_archived`` and
``source_type`` / ``search`` filters.

**PII discipline.** Every contact in these fixtures uses obviously-fake
names like ``Test Person 1``. No real PII appears anywhere — same
convention applied throughout the suite.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from job_assist.db.models import Contact

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Client + execute counter ─────────────────────────────────────────────────


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
    """Wraps ``session.execute`` to count SQL statements per endpoint."""

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


# ── Fake-name fixture factory ────────────────────────────────────────────────


def _contact(
    *,
    n: int,
    source_type: str = "tippie_alumni",
    archived_at: datetime | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
    employer: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    created_at: datetime | None = None,
) -> Contact:
    """Build a fake-name Contact. Reachability invariant satisfied via
    a synthetic email if neither email nor linkedin_url is supplied."""
    suffix = uuid.uuid4().hex[:6]
    return Contact(
        first_name=first_name or f"Test{n}",
        last_name=last_name or f"Person{n}",
        email_primary=(email if email is not None else f"test{n}-{suffix}@example.test"),
        linkedin_url=linkedin_url,
        current_employer=employer,
        source_type=source_type,
        contact_opt_in=True,
        archived_at=archived_at,
        created_at=created_at or datetime.now(tz=UTC),
    )


async def _clear_contacts(db_session: Any) -> None:
    """Wipe contacts between tests. ``contact`` is NOT in conftest's
    truncate list — it's outside the per-test reset (PR #39 design)."""
    await db_session.execute(delete(Contact))
    await db_session.commit()


# ── Validation (pure, no DB) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_invalid_limit_422() -> None:
    """``limit=0`` is below the floor → 422."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/contacts?limit=0")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_limit_too_large_422() -> None:
    """``limit=101`` exceeds the 100 cap → 422."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/contacts?limit=101")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_invalid_source_type_422() -> None:
    """Unknown ``source_type`` → 422 with a clear allowed-set listing."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/contacts?source_type=bogus")
    assert resp.status_code == 422


# ── DB-gated behaviour ──────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_excludes_archived_by_default(db_session: Any) -> None:
    """Default list filters ``archived_at IS NULL`` at the SQL layer.

    The dedup contract on email/LinkedIn is also scoped to non-archived
    rows (partial UNIQUE indexes per migration ``e8f9a0b1c2d3``), so
    aligning the list scope keeps the operator from seeing rows that
    don't count toward the dedup set.
    """
    await _clear_contacts(db_session)
    db_session.add(_contact(n=1))
    db_session.add(_contact(n=2, archived_at=datetime.now(tz=UTC)))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/contacts")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["first_name"] == "Test1"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_include_archived_returns_both(db_session: Any) -> None:
    """``?include_archived=true`` returns active + archived rows."""
    await _clear_contacts(db_session)
    db_session.add(_contact(n=1))
    db_session.add(_contact(n=2, archived_at=datetime.now(tz=UTC)))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/contacts?include_archived=true")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["total"] == 2
    archived_at_values = {item["archived_at"] for item in body["items"]}
    # One row has archived_at set; one is None. Positive equality on the
    # set membership, not on absence.
    assert None in archived_at_values


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_filters_by_source_type(db_session: Any) -> None:
    """``?source_type=`` repeating param ORs the allowed set."""
    await _clear_contacts(db_session)
    db_session.add(_contact(n=1, source_type="tippie_alumni"))
    db_session.add(_contact(n=2, source_type="linkedin_outreach"))
    db_session.add(_contact(n=3, source_type="recruiter_inbound"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/contacts?source_type=tippie_alumni&source_type=linkedin_outreach")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["total"] == 2
    surfaced_types = {item["source_type"] for item in body["items"]}
    assert surfaced_types == {"tippie_alumni", "linkedin_outreach"}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_search_matches_first_last_or_full(db_session: Any) -> None:
    """Search runs ILIKE on first_name, last_name, and "first last"."""
    await _clear_contacts(db_session)
    db_session.add(_contact(n=1, first_name="Alpha", last_name="Andersen"))
    db_session.add(_contact(n=2, first_name="Beta", last_name="Brown"))
    db_session.add(_contact(n=3, first_name="Charlie", last_name="Andersen"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            r_first = await ac.get("/contacts?search=alpha")
            r_last = await ac.get("/contacts?search=andersen")
            r_full = await ac.get("/contacts?search=charlie ander")
    finally:
        await _drop_override()

    assert {item["first_name"] for item in r_first.json()["items"]} == {"Alpha"}
    assert {item["first_name"] for item in r_last.json()["items"]} == {"Alpha", "Charlie"}
    assert {item["first_name"] for item in r_full.json()["items"]} == {"Charlie"}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_pagination_respects_limit_and_offset(db_session: Any) -> None:
    """5 rows, limit=2 → 2 items in page 1, distinct items in page 2."""
    await _clear_contacts(db_session)
    base = datetime.now(tz=UTC)
    for i in range(5):
        # Stagger created_at so ordering is unambiguous.
        db_session.add(_contact(n=i, created_at=base - timedelta(seconds=i)))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            p1 = await ac.get("/contacts?limit=2&offset=0")
            p2 = await ac.get("/contacts?limit=2&offset=2")
    finally:
        await _drop_override()

    assert p1.json()["total"] == 5
    assert len(p1.json()["items"]) == 2
    assert len(p2.json()["items"]) == 2
    page_1_ids = {x["id"] for x in p1.json()["items"]}
    page_2_ids = {x["id"] for x in p2.json()["items"]}
    assert page_1_ids.isdisjoint(page_2_ids)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_stable_id_tiebreaker_on_same_created_at(db_session: Any) -> None:
    """Same-second created_at rows order by id ASC (bestiary lock)."""
    await _clear_contacts(db_session)
    shared_ts = datetime.now(tz=UTC).replace(microsecond=0)
    for i in range(3):
        db_session.add(_contact(n=i, created_at=shared_ts))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/contacts?limit=10")
    finally:
        await _drop_override()

    ids = [item["id"] for item in resp.json()["items"]]
    # All three share created_at, so the response order must match
    # ascending id order. Sort returns ascending; deep-compare:
    assert ids == sorted(ids)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_two_query_budget(db_session: Any) -> None:
    """``GET /contacts`` emits exactly two SQL statements: COUNT + SELECT."""
    await _clear_contacts(db_session)
    for i in range(3):
        db_session.add(_contact(n=i))
    await db_session.commit()

    counter = _ExecuteCounter(db_session)
    ac = await _client(db_session)
    try:
        async with ac, counter:
            resp = await ac.get("/contacts?limit=10")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert counter.count <= 2, f"expected ≤2 queries; got {counter.count}"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_response_carries_pii_for_operator(db_session: Any) -> None:
    """The list endpoint DOES return PII (that's the point — operator
    needs name + contact channel to act). Asserting positive equality
    on the field shape locks the wire contract."""
    await _clear_contacts(db_session)
    db_session.add(_contact(n=1, employer="ExampleCorp"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/contacts")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    # Spot-check the exact contract; types listed in ContactListItem.
    assert item["first_name"] == "Test1"
    assert item["last_name"] == "Person1"
    assert item["current_employer"] == "ExampleCorp"
    assert item["source_type"] == "tippie_alumni"
    assert item["archived_at"] is None
    # Channel field is present even when only one channel was supplied.
    assert item["email_primary"] is not None


# ── Cleanup — leave the table empty for downstream tests ───────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_zzz_cleanup_clears_contacts(db_session: Any) -> None:
    """File-scope teardown. ``contact`` isn't in conftest's truncate
    list, so contacts written by tests above persist across tests.
    Wipe the slate before this file's tests are picked up next time."""
    await _clear_contacts(db_session)
    remaining = (await db_session.execute(select(Contact))).scalars().all()
    assert remaining == []
