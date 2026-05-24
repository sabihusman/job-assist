"""Read-endpoint tests for PR #30a.

Sync tests cover request-validation; DB-gated tests cover the shape +
filter + pagination + ordering + no-N+1 behaviour of:
  GET /postings           — list
  GET /postings/{id}      — detail
  GET /companies          — list with counts
  GET /outcomes           — chronological event list
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import (
    Division,
    JobPosting,
    OutcomeEvent,
    PostingSource,
    TargetCompany,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── ASGI client + query-count helper ─────────────────────────────────────────


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
    """Wraps ``session.execute`` to count how many SQL statements the
    endpoint emits. The tests assert ≤ N for the no-N+1 contract.

    Implements both sync and async context-manager protocols so it can be
    used with ``with counter:`` OR ``async with counter:`` — convenient
    when nesting alongside other ``async with`` blocks (like httpx's
    ``AsyncClient``).
    """

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


# ── Factories ────────────────────────────────────────────────────────────────


def _company(
    *,
    name: str,
    tier: int = 1,
    ats: str = "greenhouse",
    domain: str | None = None,
    description: str | None = None,
) -> TargetCompany:
    return TargetCompany(
        name=name,
        tier=tier,
        ats=ats,
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
        domain=domain,
        description=description,
    )


def _posting(
    *,
    target_company_id: uuid.UUID | None,
    title: str = "Senior Product Manager",
    role_family: str = "product_management",
    remote_type: str = "remote",
    department: str | None = None,
    team: str | None = None,
    salary_max: int | None = None,
    closed: bool = False,
    first_seen_at: datetime | None = None,
) -> JobPosting:
    now = first_seen_at or datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=title.lower(),
        raw_title=title,
        remote_type=remote_type,
        role_family=role_family,
        seniority_level="senior_pm",
        jd_text="JD body.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        salary_max=salary_max,
        department=department,
        team=team,
        closed_at=now if closed else None,
    )


def _posting_source(
    *,
    job_posting_id: uuid.UUID,
    ats: str = "greenhouse",
    url: str | None = None,
    fetched_at: datetime | None = None,
) -> PostingSource:
    return PostingSource(
        job_posting_id=job_posting_id,
        ats=ats,
        source_job_id=uuid.uuid4().hex,
        source_url=url or f"https://jobs.example.com/{uuid.uuid4().hex[:8]}",
        apply_url=None,
        raw_payload={},
        parser_version="test-v1",
        fetch_status="ok",
        fetched_at=fetched_at or datetime.now(tz=UTC),
    )


# ── /postings — sync request validation ──────────────────────────────────────


@_NEEDS_DB
async def test_postings_limit_too_large(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?limit=200")
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
async def test_postings_invalid_ats_rejected(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?ats=workday")
    finally:
        await _drop_override()
    assert resp.status_code == 422
    assert "workday" in resp.text


@_NEEDS_DB
async def test_postings_invalid_remote_type_rejected(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?remote_type=hybrid_remote")
    finally:
        await _drop_override()
    assert resp.status_code == 422


# ── /postings — DB-backed ────────────────────────────────────────────────────


@_NEEDS_DB
async def test_postings_list_empty(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"total": 0, "offset": 0, "limit": 20, "items": []}


@_NEEDS_DB
async def test_postings_default_pagination_and_total(db_session: Any) -> None:
    company = _company(name="PaginateCo")
    db_session.add(company)
    await db_session.flush()

    # 30 postings, each ~1 minute apart so the sort is deterministic.
    base = datetime(2026, 5, 1, tzinfo=UTC)
    for i in range(30):
        jp = _posting(target_company_id=company.id, first_seen_at=base + timedelta(minutes=i))
        db_session.add(jp)
        await db_session.flush()
        db_session.add(_posting_source(job_posting_id=jp.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            # Disable per-company cap — all 30 rows belong to PaginateCo
            # and the cap (default 3, PR #58) would collapse them.
            # This test's invariant is pagination math.
            resp = await ac.get("/postings?per_company_cap=0")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["total"] == 30
    assert body["offset"] == 0
    assert body["limit"] == 20
    assert len(body["items"]) == 20


@_NEEDS_DB
async def test_postings_offset_pagination(db_session: Any) -> None:
    company = _company(name="OffsetCo")
    db_session.add(company)
    await db_session.flush()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    for i in range(15):
        jp = _posting(target_company_id=company.id, first_seen_at=base + timedelta(minutes=i))
        db_session.add(jp)
        await db_session.flush()
        db_session.add(_posting_source(job_posting_id=jp.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            # Disable per-company cap (PR #58 default=3) — fixture is
            # 15 postings from one company and the cap would mask
            # the pagination math under test.
            resp = await ac.get("/postings?limit=5&offset=10&per_company_cap=0")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["total"] == 15
    assert body["limit"] == 5
    assert body["offset"] == 10
    assert len(body["items"]) == 5


@_NEEDS_DB
async def test_postings_filter_by_tier(db_session: Any) -> None:
    c1 = _company(name="T1Co", tier=1)
    c2 = _company(name="T3Co", tier=3)
    db_session.add_all([c1, c2])
    await db_session.flush()
    for c in (c1, c2):
        jp = _posting(target_company_id=c.id)
        db_session.add(jp)
        await db_session.flush()
        db_session.add(_posting_source(job_posting_id=jp.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?tier=1")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["company"]["name"] == "T1Co"


@_NEEDS_DB
async def test_postings_filter_by_tier_multi(db_session: Any) -> None:
    c1 = _company(name="MultiT1", tier=1)
    c2 = _company(name="MultiT2", tier=2)
    c3 = _company(name="MultiT3", tier=3)
    db_session.add_all([c1, c2, c3])
    await db_session.flush()
    for c in (c1, c2, c3):
        jp = _posting(target_company_id=c.id)
        db_session.add(jp)
        await db_session.flush()
        db_session.add(_posting_source(job_posting_id=jp.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?tier=1&tier=2")
    finally:
        await _drop_override()

    names = {item["company"]["name"] for item in resp.json()["items"]}
    assert names == {"MultiT1", "MultiT2"}


@_NEEDS_DB
async def test_postings_filter_by_ats(db_session: Any) -> None:
    c = _company(name="AtsFilterCo")
    db_session.add(c)
    await db_session.flush()
    jp_gh = _posting(target_company_id=c.id)
    jp_lev = _posting(target_company_id=c.id)
    db_session.add_all([jp_gh, jp_lev])
    await db_session.flush()
    db_session.add_all(
        [
            _posting_source(job_posting_id=jp_gh.id, ats="greenhouse"),
            _posting_source(job_posting_id=jp_lev.id, ats="lever"),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?ats=greenhouse")
    finally:
        await _drop_override()

    items = resp.json()["items"]
    assert {item["source"]["ats"] for item in items} == {"greenhouse"}


@_NEEDS_DB
async def test_postings_filter_by_remote_type(db_session: Any) -> None:
    c = _company(name="RemoteFilterCo")
    db_session.add(c)
    await db_session.flush()
    db_session.add_all(
        [
            _posting(target_company_id=c.id, remote_type="remote"),
            _posting(target_company_id=c.id, remote_type="onsite"),
        ]
    )
    await db_session.flush()
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?remote_type=remote")
    finally:
        await _drop_override()

    assert {item["remote_type"] for item in resp.json()["items"]} == {"remote"}


@_NEEDS_DB
async def test_postings_filter_by_role_family_case_insensitive(db_session: Any) -> None:
    c = _company(name="RoleFamCo")
    db_session.add(c)
    await db_session.flush()
    db_session.add_all(
        [
            _posting(target_company_id=c.id, role_family="product_management"),
            _posting(target_company_id=c.id, role_family="product_marketing"),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            # Uppercase input must still match the lowercase enum value.
            resp = await ac.get("/postings?role_family=PRODUCT_MANAGEMENT")
    finally:
        await _drop_override()

    assert all(item["role"]["family"] == "product_management" for item in resp.json()["items"])


@_NEEDS_DB
async def test_postings_filter_by_target_company_id(db_session: Any) -> None:
    c1 = _company(name="TargetA")
    c2 = _company(name="TargetB")
    db_session.add_all([c1, c2])
    await db_session.flush()
    db_session.add_all(
        [
            _posting(target_company_id=c1.id),
            _posting(target_company_id=c2.id),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings?target_company_id={c1.id}")
    finally:
        await _drop_override()

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["company"]["id"] == str(c1.id)


@_NEEDS_DB
async def test_postings_sorted_first_seen_desc(db_session: Any) -> None:
    c = _company(name="SortedCo")
    db_session.add(c)
    await db_session.flush()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    db_session.add_all(
        [
            _posting(target_company_id=c.id, first_seen_at=base),
            _posting(target_company_id=c.id, first_seen_at=base + timedelta(days=1)),
            _posting(target_company_id=c.id, first_seen_at=base + timedelta(days=2)),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings")
    finally:
        await _drop_override()

    ts = [item["first_seen_at"] for item in resp.json()["items"]]
    assert ts == sorted(ts, reverse=True)


@_NEEDS_DB
async def test_postings_response_shape(db_session: Any) -> None:
    """Golden shape on one fully-populated row."""
    c = _company(
        name="ShapeCo",
        tier=1,
        domain="shape.example",
        description="ShapeCo builds shapes.",
    )
    db_session.add(c)
    await db_session.flush()
    jp = _posting(
        target_company_id=c.id,
        title="Senior PM",
        department="Product",
        team="Risk",
        salary_max=180_000,
    )
    db_session.add(jp)
    await db_session.flush()
    db_session.add(
        _posting_source(
            job_posting_id=jp.id,
            ats="greenhouse",
            url="https://jobs.shape.example/123",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings")
    finally:
        await _drop_override()

    item = resp.json()["items"][0]
    assert set(item) == {
        "id",
        "company",
        "role",
        "location_raw",
        "locations_normalized",
        "remote_type",
        "salary",
        "source",
        "first_seen_at",
        "score",
        # Added in PR #31 — always present, nested fields null for untouched postings.
        "state",
    }
    assert item["company"]["name"] == "ShapeCo"
    assert item["company"]["domain"] == "shape.example"
    assert item["company"]["tier"] == 1
    assert item["role"]["department"] == "Product"
    assert item["role"]["team"] == "Risk"
    assert item["salary"]["max"] == 180_000
    assert item["source"]["ats"] == "greenhouse"
    assert item["source"]["url"] == "https://jobs.shape.example/123"
    assert item["score"] is None


@_NEEDS_DB
async def test_postings_no_n_plus_one(db_session: Any) -> None:
    """20 postings across 5 companies → 2 SQL statements total.

    PR #58: the default ``per_company_cap=3`` would clip this fixture
    from 20 to 15 (each company has 4 postings; cap takes 3). The
    no-N+1 test predates the cap and wants to count ALL 20 rows to
    prove the lateral joins don't multiply queries — we explicitly
    disable the cap here so the test's invariant survives the new
    default. The cap itself is covered by tests/test_per_company_cap.py.
    """
    companies = [_company(name=f"NPlusOne{i}", tier=i % 4 + 1) for i in range(5)]
    db_session.add_all(companies)
    await db_session.flush()
    for c in companies:
        for _ in range(4):
            jp = _posting(target_company_id=c.id)
            db_session.add(jp)
            await db_session.flush()
            db_session.add(_posting_source(job_posting_id=jp.id))
    await db_session.commit()

    counter = _ExecuteCounter(db_session)
    ac = await _client(db_session)
    try:
        async with ac, counter:
            resp = await ac.get("/postings?per_company_cap=0")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert resp.json()["total"] == 20
    assert counter.count <= 2, f"expected ≤2 queries, got {counter.count}"


# ── /postings/{id} ───────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_posting_detail_full_shape(db_session: Any) -> None:
    c = _company(name="DetailCo", description="DetailCo description")
    db_session.add(c)
    await db_session.flush()
    jp = _posting(target_company_id=c.id, department="Eng", team="Platform")
    db_session.add(jp)
    await db_session.flush()
    db_session.add(_posting_source(job_posting_id=jp.id))
    db_session.add(
        Division(
            target_company_id=c.id,
            department="Eng",
            team="Platform",
            description="The Eng/Platform division ships infra.",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings/{jp.id}")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(jp.id)
    assert body["description_markdown"] == "JD body."
    assert body["division"] is not None
    assert body["division"]["department"] == "Eng"
    assert body["division"]["team"] == "Platform"
    assert "infra" in body["division"]["description"]


@_NEEDS_DB
async def test_posting_detail_404_unknown_id(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings/{uuid.uuid4()}")
    finally:
        await _drop_override()
    assert resp.status_code == 404


@_NEEDS_DB
async def test_posting_detail_division_null_when_no_match(db_session: Any) -> None:
    c = _company(name="NoDivCo")
    db_session.add(c)
    await db_session.flush()
    jp = _posting(target_company_id=c.id, department="Eng", team=None)
    db_session.add(jp)
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings/{jp.id}")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert resp.json()["division"] is None


@_NEEDS_DB
async def test_posting_detail_division_matches_via_nulls(db_session: Any) -> None:
    """Posting (Eng, NULL) + division (Eng, NULL) match via IS NOT DISTINCT FROM."""
    c = _company(name="NullsMatch")
    db_session.add(c)
    await db_session.flush()
    jp = _posting(target_company_id=c.id, department="Eng", team=None)
    db_session.add(jp)
    db_session.add(
        Division(
            target_company_id=c.id,
            department="Eng",
            team=None,
            description="The Eng division",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings/{jp.id}")
    finally:
        await _drop_override()

    body = resp.json()
    assert body["division"] is not None
    assert body["division"]["department"] == "Eng"
    assert body["division"]["team"] is None


# ── /companies ───────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_companies_list_returns_counts(db_session: Any) -> None:
    c1 = _company(name="CountsA", tier=1)
    c2 = _company(name="CountsB", tier=2)
    c3 = _company(name="CountsC", tier=3)
    db_session.add_all([c1, c2, c3])
    await db_session.flush()
    for _ in range(5):
        db_session.add(_posting(target_company_id=c1.id))
    for _ in range(3):
        db_session.add(_posting(target_company_id=c2.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/companies")
    finally:
        await _drop_override()

    by_name = {c["name"]: c for c in resp.json()["items"]}
    assert by_name["CountsA"]["total_postings"] == 5
    assert by_name["CountsB"]["total_postings"] == 3
    assert by_name["CountsC"]["total_postings"] == 0


@_NEEDS_DB
async def test_companies_active_vs_total(db_session: Any) -> None:
    c = _company(name="ActiveVsTotal")
    db_session.add(c)
    await db_session.flush()
    for closed in (False, False, False, True, True):
        db_session.add(_posting(target_company_id=c.id, closed=closed))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/companies")
    finally:
        await _drop_override()

    row = next(c for c in resp.json()["items"] if c["name"] == "ActiveVsTotal")
    assert row["total_postings"] == 5
    assert row["active_postings"] == 3


@_NEEDS_DB
async def test_companies_ats_set(db_session: Any) -> None:
    c = _company(name="AtsSetCo")
    db_session.add(c)
    await db_session.flush()
    jp1 = _posting(target_company_id=c.id)
    jp2 = _posting(target_company_id=c.id)
    db_session.add_all([jp1, jp2])
    await db_session.flush()
    db_session.add_all(
        [
            _posting_source(job_posting_id=jp1.id, ats="greenhouse"),
            _posting_source(job_posting_id=jp2.id, ats="lever"),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/companies")
    finally:
        await _drop_override()

    row = next(c for c in resp.json()["items"] if c["name"] == "AtsSetCo")
    assert set(row["ats_set"]) == {"greenhouse", "lever"}


@_NEEDS_DB
async def test_companies_filter_by_tier(db_session: Any) -> None:
    c1 = _company(name="CompTierA", tier=1)
    c2 = _company(name="CompTierB", tier=3)
    db_session.add_all([c1, c2])
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/companies?tier=3")
    finally:
        await _drop_override()

    items = resp.json()["items"]
    assert {c["name"] for c in items} == {"CompTierB"}


@_NEEDS_DB
async def test_companies_sort_tier_then_name(db_session: Any) -> None:
    db_session.add_all(
        [
            _company(name="Zeta T2", tier=2),
            _company(name="Alpha T3", tier=3),
            _company(name="Mid T1", tier=1),
            _company(name="Bravo T1", tier=1),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/companies")
    finally:
        await _drop_override()

    names = [c["name"] for c in resp.json()["items"]]
    # Filter to the four we just inserted so existing fixtures don't pollute.
    ours = [n for n in names if n in {"Bravo T1", "Mid T1", "Zeta T2", "Alpha T3"}]
    assert ours == ["Bravo T1", "Mid T1", "Zeta T2", "Alpha T3"]


@_NEEDS_DB
async def test_companies_no_n_plus_one(db_session: Any) -> None:
    """5 companies x 10 postings each -> 2 SQL statements total."""
    companies = [_company(name=f"NPlus1Co{i}", tier=i % 4 + 1) for i in range(5)]
    db_session.add_all(companies)
    await db_session.flush()
    for c in companies:
        for _ in range(10):
            jp = _posting(target_company_id=c.id)
            db_session.add(jp)
            await db_session.flush()
            db_session.add(_posting_source(job_posting_id=jp.id))
    await db_session.commit()

    counter = _ExecuteCounter(db_session)
    ac = await _client(db_session)
    try:
        async with ac, counter:
            resp = await ac.get("/companies")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert counter.count <= 2, f"expected ≤2 queries, got {counter.count}"


# ── /outcomes ────────────────────────────────────────────────────────────────


def _outcome(
    *,
    received_at: datetime,
    job_posting_id: uuid.UUID | None = None,
    outcome_type: str = "rejection_pre_screen",
) -> OutcomeEvent:
    return OutcomeEvent(
        job_posting_id=job_posting_id,
        email_message_id=f"msg-{uuid.uuid4().hex}",
        from_address="r@example.com",
        from_domain="example.com",
        subject="x",
        received_at=received_at,
        outcome_type=outcome_type,
        classifier_version="gemini-flash-lite-v1",
        classifier_confidence=0.9,
    )


@_NEEDS_DB
async def test_outcomes_chronological(db_session: Any) -> None:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    db_session.add_all(
        [
            _outcome(received_at=base + timedelta(days=2)),
            _outcome(received_at=base + timedelta(days=0)),
            _outcome(received_at=base + timedelta(days=1)),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/outcomes")
    finally:
        await _drop_override()

    ts = [item["received_at"] for item in resp.json()["items"]]
    assert ts == sorted(ts)


@_NEEDS_DB
async def test_outcomes_filter_by_posting_id(db_session: Any) -> None:
    c = _company(name="OutcomesFilterCo")
    db_session.add(c)
    await db_session.flush()
    jp_a = _posting(target_company_id=c.id)
    jp_b = _posting(target_company_id=c.id)
    db_session.add_all([jp_a, jp_b])
    await db_session.flush()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    db_session.add_all(
        [
            _outcome(received_at=base, job_posting_id=jp_a.id),
            _outcome(received_at=base + timedelta(hours=1), job_posting_id=jp_b.id),
        ]
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/outcomes?posting_id={jp_a.id}")
    finally:
        await _drop_override()

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["posting_id"] == str(jp_a.id)


@_NEEDS_DB
async def test_outcomes_response_shape(db_session: Any) -> None:
    db_session.add(
        _outcome(
            received_at=datetime(2026, 5, 1, tzinfo=UTC),
            outcome_type="application_confirmation",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/outcomes")
    finally:
        await _drop_override()

    items = resp.json()["items"]
    assert items, "expected at least one outcome row"
    sample = items[-1]  # newest at the end of ASC order
    assert set(sample) == {"id", "posting_id", "received_at", "stage", "confidence"}
    assert sample["stage"] == "application_confirmation"
    assert sample["confidence"] == pytest.approx(0.9)
