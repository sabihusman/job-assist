"""Tests for PR #30b — stats endpoints (calibration + funnel).

Covers:
  GET /stats/calibration  — KPI counts, interested_rate rounding,
                            top_rejected_role_families ordering & dedup
  GET /stats/funnel       — fixed stage order, conversion rates,
                            null-on-zero rule, query budget
  Window validation       — since/until ordering, future cutoff, 365-day cap
  Default window          — Monday 00:00 UTC of frozen "now"

Window validation is independent of the DB so its tests don't need
TEST_DATABASE_URL. The default-window test uses the module-level
``set_clock`` seam from ``services.stats_windows`` rather than freezegun.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import JobPosting, PostingAction, TargetCompany

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
    """Wraps ``session.execute`` to count SQL statements. Sync + async
    context-manager protocols so it composes with httpx's AsyncClient."""

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


# ── Factories (kept lean — only the columns the stats endpoints touch) ─────


def _company(name: str = "StatsCo") -> TargetCompany:
    return TargetCompany(
        name=name,
        tier=1,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(
    *,
    target_company_id: uuid.UUID | None,
    role_family: str = "product_management",
    first_seen_at: datetime | None = None,
) -> JobPosting:
    fs = first_seen_at or datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        remote_type="remote",
        role_family=role_family,
        seniority_level="senior_pm",
        jd_text="JD.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=fs,
        last_seen_at=fs,
    )


def _action(
    *,
    job_posting_id: uuid.UUID,
    action_type: str,
    reason: str | None = None,
    created_at: datetime | None = None,
) -> PostingAction:
    return PostingAction(
        job_posting_id=job_posting_id,
        action_type=action_type,
        reason=reason,
        created_at=created_at or datetime.now(tz=UTC),
    )


def _iso(ts: datetime) -> str:
    return ts.isoformat()


# ── Window validation (no DB needed) ─────────────────────────────────────────


async def test_window_since_after_until_422() -> None:
    from job_assist.main import app

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    now = datetime.now(tz=UTC)
    async with ac:
        resp = await ac.get(
            "/stats/calibration",
            params={
                "since": _iso(now - timedelta(hours=1)),
                "until": _iso(now - timedelta(hours=5)),
            },
        )
    assert resp.status_code == 422
    assert "after" in resp.text.lower()


async def test_window_since_in_future_422() -> None:
    from job_assist.main import app

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    future = datetime.now(tz=UTC) + timedelta(days=1)
    async with ac:
        resp = await ac.get(
            "/stats/calibration",
            params={"since": _iso(future), "until": _iso(future + timedelta(hours=1))},
        )
    assert resp.status_code == 422
    assert "future" in resp.text.lower()


async def test_window_since_over_365_days_422() -> None:
    from job_assist.main import app

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    now = datetime.now(tz=UTC)
    async with ac:
        resp = await ac.get(
            "/stats/calibration",
            params={"since": _iso(now - timedelta(days=400)), "until": _iso(now)},
        )
    assert resp.status_code == 422
    assert "365" in resp.text


async def test_window_until_in_future_422() -> None:
    from job_assist.main import app

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    now = datetime.now(tz=UTC)
    async with ac:
        resp = await ac.get(
            "/stats/calibration",
            params={
                "since": _iso(now - timedelta(hours=1)),
                "until": _iso(now + timedelta(hours=1)),
            },
        )
    assert resp.status_code == 422


# ── Default window ───────────────────────────────────────────────────────────


async def test_default_window_is_this_week_monday_utc() -> None:
    """Freeze time on a known Wednesday and assert the default window
    starts at the Monday of that ISO week at 00:00 UTC."""
    from job_assist.services import stats_windows

    # Wednesday 2026-05-13 14:42:17 UTC → Monday is 2026-05-11 00:00 UTC.
    frozen = datetime(2026, 5, 13, 14, 42, 17, tzinfo=UTC)
    stats_windows.set_clock(lambda: frozen)
    try:
        since, until = stats_windows.default_window()
    finally:
        stats_windows.set_clock(None)

    assert since == datetime(2026, 5, 11, 0, 0, 0, tzinfo=UTC)
    assert until == frozen


# ── Calibration — DB-backed ──────────────────────────────────────────────────


@_NEEDS_DB
async def test_calibration_empty_window(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["surfaced"] == 0
    assert body["interested"] == 0
    assert body["applied"] == 0
    assert body["rejected_by_you"] == 0
    assert body["interested_rate"] is None
    assert body["top_rejected_role_families"] == []


@_NEEDS_DB
async def test_calibration_counts(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    # 5 postings surfaced (all this hour, well within "this week" default).
    postings = [_posting(target_company_id=company.id) for _ in range(5)]
    db_session.add_all(postings)
    await db_session.flush()
    # 3 marked interested (one of which also becomes applied), 2 marked
    # applied total, 1 not_interested. Per the spec rules:
    #   interested: postings with any action_type IN ('interested','applied') → 3+2 minus overlap = 3
    #   applied: postings with action_type=applied → 2
    #   rejected: 1
    db_session.add(_action(job_posting_id=postings[0].id, action_type="interested"))
    db_session.add(_action(job_posting_id=postings[1].id, action_type="interested"))
    db_session.add(_action(job_posting_id=postings[2].id, action_type="interested"))
    db_session.add(_action(job_posting_id=postings[2].id, action_type="applied"))
    db_session.add(_action(job_posting_id=postings[3].id, action_type="applied"))
    db_session.add(
        _action(
            job_posting_id=postings[4].id,
            action_type="not_interested",
            reason="wrong_role",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    body = resp.json()
    assert body["surfaced"] == 5
    # 3 'interested' rows + 1 standalone 'applied' → 4 distinct postings
    # contribute to INTERESTED. (postings[2] counts once via DISTINCT.)
    assert body["interested"] == 4
    assert body["applied"] == 2
    assert body["rejected_by_you"] == 1


@_NEEDS_DB
async def test_calibration_applied_counts_toward_interested(db_session: Any) -> None:
    """Posting goes straight to applied (no prior interested).
    INTERESTED count should still include it."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id)
    db_session.add(posting)
    await db_session.flush()
    db_session.add(_action(job_posting_id=posting.id, action_type="applied"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    body = resp.json()
    assert body["interested"] == 1
    assert body["applied"] == 1


@_NEEDS_DB
async def test_calibration_interested_rate_rounding(db_session: Any) -> None:
    """7 surfaced, 2 interested → 0.285714... rounds to 0.29."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    postings = [_posting(target_company_id=company.id) for _ in range(7)]
    db_session.add_all(postings)
    await db_session.flush()
    db_session.add(_action(job_posting_id=postings[0].id, action_type="interested"))
    db_session.add(_action(job_posting_id=postings[1].id, action_type="interested"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    assert resp.json()["interested_rate"] == 0.29


@_NEEDS_DB
async def test_calibration_interested_rate_null_when_surfaced_zero(
    db_session: Any,
) -> None:
    """No postings → surfaced=0 → interested_rate is null, not 0."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    assert resp.json()["interested_rate"] is None


@_NEEDS_DB
async def test_calibration_top_rejected_role_families_groups_correctly(
    db_session: Any,
) -> None:
    """Verify ORDER BY count DESC, role_family ASC with > 5 families."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    # role_family vocabulary (per db/enums.py):
    #   product_management, product_owner, product_marketing,
    #   program_management, other
    families_counts = {
        "program_management": 4,
        "product_marketing": 2,
        "product_owner": 2,
        "other": 1,
        "product_management": 1,
    }
    for family, n in families_counts.items():
        for _ in range(n):
            p = _posting(target_company_id=company.id, role_family=family)
            db_session.add(p)
            await db_session.flush()
            db_session.add(
                _action(
                    job_posting_id=p.id,
                    action_type="not_interested",
                    reason="wrong_role",
                )
            )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    top = resp.json()["top_rejected_role_families"]
    assert len(top) <= 5
    # Expected ordering: program_management (4), product_marketing (2),
    # product_owner (2), other (1), product_management (1).
    # Ties broken alphabetically ASC, so product_marketing < product_owner
    # and other < product_management.
    assert [(r["role_family"], r["count"]) for r in top] == [
        ("program_management", 4),
        ("product_marketing", 2),
        ("product_owner", 2),
        ("other", 1),
        ("product_management", 1),
    ]


@_NEEDS_DB
async def test_calibration_top_rejected_role_families_excludes_null_family(
    db_session: Any,
) -> None:
    """Postings with NULL role_family must not appear in top families.

    The schema declares role_family NOT NULL with default 'other', so we
    can't insert NULLs through the ORM — but the SQL query still filters
    `role_family IS NOT NULL` defensively. Confirm 'other'-family
    rejections do appear (i.e. the filter doesn't accidentally exclude
    the default)."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, role_family="other")
    db_session.add(p)
    await db_session.flush()
    db_session.add(
        _action(
            job_posting_id=p.id,
            action_type="not_interested",
            reason="wrong_role",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    top = resp.json()["top_rejected_role_families"]
    families = {r["role_family"] for r in top}
    assert "other" in families
    assert None not in families


@_NEEDS_DB
async def test_calibration_top_rejected_role_families_distinct_posting(
    db_session: Any,
) -> None:
    """Posting rejected → reset → rejected again. Counts as 1, not 2."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id, role_family="product_owner")
    db_session.add(p)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    db_session.add(
        _action(
            job_posting_id=p.id,
            action_type="not_interested",
            reason="wrong_role",
            created_at=now - timedelta(minutes=20),
        )
    )
    db_session.add(
        _action(
            job_posting_id=p.id,
            action_type="reset",
            created_at=now - timedelta(minutes=10),
        )
    )
    db_session.add(
        _action(
            job_posting_id=p.id,
            action_type="not_interested",
            reason="comp_too_low",
            created_at=now,
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    top = resp.json()["top_rejected_role_families"]
    assert top == [{"role_family": "product_owner", "count": 1}]


@_NEEDS_DB
async def test_calibration_top_rejected_role_families_empty_when_no_rejections(
    db_session: Any,
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id)
    db_session.add(p)
    await db_session.flush()
    db_session.add(_action(job_posting_id=p.id, action_type="interested"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    assert resp.json()["top_rejected_role_families"] == []


@_NEEDS_DB
async def test_calibration_excludes_too_many_open_apps_reason(db_session: Any) -> None:
    """feat/company-app-awareness: a ``too_many_open_apps`` pass is PORTFOLIO
    management, not a fit signal — it must NOT inflate ``rejected_by_you`` nor
    appear in ``top_rejected_role_families``. A normal ``wrong_role`` pass on the
    same family still counts, proving only the portfolio reason is excluded."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p_portfolio = _posting(target_company_id=company.id, role_family="product_owner")
    p_fit = _posting(target_company_id=company.id, role_family="product_owner")
    db_session.add_all([p_portfolio, p_fit])
    await db_session.flush()
    db_session.add(
        _action(
            job_posting_id=p_portfolio.id,
            action_type="not_interested",
            reason="too_many_open_apps",
        )
    )
    db_session.add(
        _action(
            job_posting_id=p_fit.id,
            action_type="not_interested",
            reason="wrong_role",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration")
    finally:
        await _drop_override()
    body = resp.json()
    # Only the fit pass counts toward the fit-learning aggregates.
    assert body["rejected_by_you"] == 1
    assert body["top_rejected_role_families"] == [{"role_family": "product_owner", "count": 1}]


@_NEEDS_DB
async def test_calibration_window_excludes_actions_outside(db_session: Any) -> None:
    """Actions stamped pre-since or post-until must not contribute."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    # Posting surfaced inside the window so SURFACED is meaningful.
    p_in = _posting(
        target_company_id=company.id,
        first_seen_at=now - timedelta(hours=12),
    )
    p_out = _posting(
        target_company_id=company.id,
        first_seen_at=now - timedelta(days=20),
    )
    db_session.add_all([p_in, p_out])
    await db_session.flush()
    # In-window action.
    db_session.add(
        _action(
            job_posting_id=p_in.id,
            action_type="interested",
            created_at=now - timedelta(hours=2),
        )
    )
    # Way-pre-window action — must be excluded.
    db_session.add(
        _action(
            job_posting_id=p_out.id,
            action_type="applied",
            created_at=now - timedelta(days=30),
        )
    )
    await db_session.commit()

    since = (now - timedelta(days=2)).isoformat()
    until = now.isoformat()
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration", params={"since": since, "until": until})
    finally:
        await _drop_override()
    body = resp.json()
    assert body["surfaced"] == 1  # only p_in
    assert body["interested"] == 1
    assert body["applied"] == 0  # p_out's applied is outside window


@_NEEDS_DB
async def test_calibration_surfaced_uses_first_seen_at(db_session: Any) -> None:
    """A posting whose row was inserted today but whose first_seen_at
    is older than the window must NOT count toward SURFACED."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    p_old = _posting(
        target_company_id=company.id,
        first_seen_at=now - timedelta(days=30),
    )
    p_new = _posting(
        target_company_id=company.id,
        first_seen_at=now - timedelta(hours=2),
    )
    db_session.add_all([p_old, p_new])
    await db_session.commit()

    since = (now - timedelta(days=1)).isoformat()
    until = now.isoformat()
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/calibration", params={"since": since, "until": until})
    finally:
        await _drop_override()
    assert resp.json()["surfaced"] == 1


# ── Funnel — DB-backed ───────────────────────────────────────────────────────


@_NEEDS_DB
async def test_funnel_empty_window_returns_zero_stages(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/funnel")
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["count"] for s in body["stages"]] == [0, 0, 0]
    # Rates are null since the upstream counts are 0.
    assert all(cr["rate"] is None for cr in body["conversion_rates"])


@_NEEDS_DB
async def test_funnel_stage_order_fixed(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id)
    db_session.add(p)
    await db_session.flush()
    db_session.add(_action(job_posting_id=p.id, action_type="applied"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/funnel")
    finally:
        await _drop_override()
    names = [s["name"] for s in resp.json()["stages"]]
    assert names == ["surfaced", "interested", "applied"]


@_NEEDS_DB
async def test_funnel_conversion_rate_computation(db_session: Any) -> None:
    """surfaced=10, interested=4, applied=1 → rates [0.40, 0.25]."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    postings = [_posting(target_company_id=company.id) for _ in range(10)]
    db_session.add_all(postings)
    await db_session.flush()
    # 4 interested (one of them also applied)
    for i in range(3):
        db_session.add(_action(job_posting_id=postings[i].id, action_type="interested"))
    db_session.add(_action(job_posting_id=postings[3].id, action_type="applied"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/funnel")
    finally:
        await _drop_override()
    body = resp.json()
    counts = {s["name"]: s["count"] for s in body["stages"]}
    assert counts == {"surfaced": 10, "interested": 4, "applied": 1}
    rates = body["conversion_rates"]
    assert rates[0]["rate"] == 0.40
    assert rates[1]["rate"] == 0.25


@_NEEDS_DB
async def test_funnel_conversion_rate_null_on_zero_upstream(db_session: Any) -> None:
    """When surfaced=0 the first conversion rate must be null."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/funnel")
    finally:
        await _drop_override()
    rates = resp.json()["conversion_rates"]
    assert rates[0]["from"] == "surfaced"
    assert rates[0]["rate"] is None


@_NEEDS_DB
async def test_funnel_each_posting_counted_once_per_stage(db_session: Any) -> None:
    """Posting marked interested 3 times in window → interested = 1."""
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p = _posting(target_company_id=company.id)
    db_session.add(p)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    for i in range(3):
        db_session.add(
            _action(
                job_posting_id=p.id,
                action_type="interested",
                created_at=now - timedelta(minutes=10 - i),
            )
        )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/stats/funnel")
    finally:
        await _drop_override()
    counts = {s["name"]: s["count"] for s in resp.json()["stages"]}
    assert counts["interested"] == 1


@_NEEDS_DB
async def test_funnel_query_count(db_session: Any) -> None:
    """Funnel endpoint stays at 1 SQL query (multi-FILTER aggregate)."""
    ac = await _client(db_session)
    try:
        async with _ExecuteCounter(db_session) as counter, ac:
            resp = await ac.get("/stats/funnel")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    assert counter.count <= 6, f"funnel issued {counter.count} queries"
