"""Tests for ``GET /postings?state=...`` semantics added in PR #50.

Covers the two cross-page behaviours:

  * ``state=not_interested`` — frontend ``/passed`` page. Latest
    posting_action.action_type filter. Already shipped pre-PR-50; tests
    here lock down the latest-row semantics so a future refactor of the
    LATERAL doesn't quietly regress.
  * ``state=rejected`` — frontend ``/rejected`` page. NEW in PR #50.
    EXISTS predicate against outcome_event with the rejection-flavored
    outcome_type values (explicit IN list — see ``main.py`` for the
    bestiary note).

Sync tests cover request validation (422 on unknown wire value).
DB-gated tests assert ordering + filter semantics on a small fixture.
Every PostingSource construction mirrors the canonical factory shape
in ``tests/test_read_endpoints.py`` — 8 NOT NULL columns, no shortcuts.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import (
    JobPosting,
    OutcomeEvent,
    PostingAction,
    PostingSource,
    TargetCompany,
)

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


# ── Sync validation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_state_returns_422() -> None:
    """``state=passed`` is operator-vocab on the frontend, NOT a wire value.
    Wire vocabulary stays ``not_interested``. Same 422 path catches typos."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/postings?state=passed")
    assert resp.status_code == 422


def test_rejected_in_allowed_state_set() -> None:
    """Direct schema-level check that the PR #50 wire vocab is wired up.

    Locks the contract without hitting the handler — running ``state=rejected``
    through the endpoint requires a real DB session, covered by the DB-gated
    tests below.
    """
    from job_assist.main import _ALLOWED_STATE_FILTER_VALUES, _REJECTION_OUTCOME_TYPES

    assert "rejected" in _ALLOWED_STATE_FILTER_VALUES
    assert "not_interested" in _ALLOWED_STATE_FILTER_VALUES
    # Explicit IN list — guards against drift if the OutcomeType enum
    # grows new rejection_* values.
    assert set(_REJECTION_OUTCOME_TYPES) == {
        "rejection_pre_screen",
        "rejection_post_screen",
        "rejection_post_interview",
    }


# ── Fixture factories — mirror tests/test_read_endpoints.py exactly ─────────


def _company(name: str, tier: int = 1) -> TargetCompany:
    return TargetCompany(
        name=name,
        tier=tier,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(
    *,
    target_company_id: uuid.UUID | None,
    first_seen_at: datetime | None = None,
) -> JobPosting:
    now = first_seen_at or datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        jd_text="JD body.",
        jd_text_hash=f"{'0' * 54}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
    )


def _posting_source(*, job_posting_id: uuid.UUID) -> PostingSource:
    """Full PostingSource factory mirroring tests/test_read_endpoints.py.

    posting_source has 8 NOT NULL columns; missing any of them trips an
    asyncpg NotNullViolationError at flush time. Full kwarg set, no
    shortcuts — same bestiary lesson as PR #49.
    """
    return PostingSource(
        job_posting_id=job_posting_id,
        ats="greenhouse",
        source_job_id=uuid.uuid4().hex,
        source_url=f"https://jobs.example.com/{uuid.uuid4().hex[:8]}",
        apply_url=None,
        raw_payload={},
        parser_version="test-v1",
        fetch_status="ok",
        fetched_at=datetime.now(tz=UTC),
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
        snooze_until=None,
        notes=None,
        created_at=created_at or datetime.now(tz=UTC),
    )


def _outcome(
    *,
    job_posting_id: uuid.UUID | None,
    outcome_type: str,
    received_at: datetime | None = None,
) -> OutcomeEvent:
    return OutcomeEvent(
        job_posting_id=job_posting_id,
        target_company_id=None,
        email_message_id=f"msg-{uuid.uuid4().hex}",
        email_thread_id=None,
        from_address="recruiter@example.test",
        from_domain="example.test",
        subject="Test outcome",
        received_at=received_at or datetime.now(tz=UTC),
        outcome_type=outcome_type,  # type: ignore[arg-type]
        classifier_version="test-v1",
        classifier_confidence=0.95,
        raw_snippet=None,
    )


# ── state=not_interested (frontend /passed) ─────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_passed_only_latest_action_counts(db_session: Any) -> None:
    """A posting that was once not_interested then later reset → NOT in /passed.

    Locks down the latest-row semantics. ``state=not_interested`` must
    match only postings whose MOST RECENT posting_action row has
    action_type='not_interested'.
    """
    now = datetime.now(tz=UTC)
    company = _company("Co")
    db_session.add(company)
    await db_session.flush()

    # Posting A: passed and stayed passed.
    pa_passed = _posting(target_company_id=company.id)
    # Posting B: passed then reset back to triage.
    pa_reset = _posting(target_company_id=company.id)
    # Posting C: never touched (triage state, not passed).
    pa_fresh = _posting(target_company_id=company.id)
    db_session.add_all([pa_passed, pa_reset, pa_fresh])
    await db_session.flush()
    for jp in (pa_passed, pa_reset, pa_fresh):
        db_session.add(_posting_source(job_posting_id=jp.id))

    # Action history. created_at order matters for the latest-row LATERAL.
    db_session.add(
        _action(
            job_posting_id=pa_passed.id,
            action_type="not_interested",
            reason="wrong_role",
            created_at=now - timedelta(hours=2),
        )
    )
    db_session.add(
        _action(
            job_posting_id=pa_reset.id,
            action_type="not_interested",
            reason="wrong_role",
            created_at=now - timedelta(hours=2),
        )
    )
    db_session.add(
        _action(
            job_posting_id=pa_reset.id,
            action_type="reset",
            created_at=now - timedelta(hours=1),
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=not_interested&limit=100")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {str(pa_passed.id)}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_passed_surfaces_reason_inline(db_session: Any) -> None:
    """The list response carries ``state.reason`` for not_interested rows.

    Frontend ``/passed`` page renders this inline on each card — no
    secondary fetch required.
    """
    company = _company("Co")
    db_session.add(company)
    await db_session.flush()

    jp = _posting(target_company_id=company.id)
    db_session.add(jp)
    await db_session.flush()
    db_session.add(_posting_source(job_posting_id=jp.id))
    db_session.add(_action(job_posting_id=jp.id, action_type="not_interested", reason="too_senior"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=not_interested")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["state"]["current"] == "not_interested"
    assert items[0]["state"]["reason"] == "too_senior"


# ── state=rejected (frontend /rejected) ─────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_excludes_untouched_company_rejections(db_session: Any) -> None:
    """Company-level rejection outcomes do NOT surface untouched postings in
    /rejected (fix/applied-tab-fanout).

    ``outcome_event.job_posting_id`` is always NULL, so a company-level
    rejection (any of the three rejection_* types) would otherwise tag EVERY
    role at the company — passed and never-touched alike. Membership is now
    posting-specific (manual ``application_state='rejected'``); the Gmail
    rejection is an informational hint only. So none of these untouched
    postings appear.
    """
    co_pre = _company("Co-Pre")
    co_post = _company("Co-Post")
    co_interview = _company("Co-Interview")
    db_session.add_all([co_pre, co_post, co_interview])
    await db_session.flush()

    jp_pre = _posting(target_company_id=co_pre.id)
    jp_post = _posting(target_company_id=co_post.id)
    jp_interview = _posting(target_company_id=co_interview.id)
    db_session.add_all([jp_pre, jp_post, jp_interview])
    await db_session.flush()
    for jp in (jp_pre, jp_post, jp_interview):
        db_session.add(_posting_source(job_posting_id=jp.id))

    ev_pre = _outcome(job_posting_id=None, outcome_type="rejection_pre_screen")
    ev_pre.target_company_id = co_pre.id
    ev_post = _outcome(job_posting_id=None, outcome_type="rejection_post_screen")
    ev_post.target_company_id = co_post.id
    ev_interview = _outcome(job_posting_id=None, outcome_type="rejection_post_interview")
    ev_interview.target_company_id = co_interview.id
    db_session.add_all([ev_pre, ev_post, ev_interview])
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=rejected&limit=100")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    # Untouched postings with only company-level Gmail rejections never surface.
    assert resp.json()["items"] == []


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_ignores_non_rejection_outcomes(db_session: Any) -> None:
    """Postings with only non-rejection outcomes (interview invites, offers)
    must NOT appear in ``state=rejected``."""
    company = _company("Co")
    db_session.add(company)
    await db_session.flush()

    jp_offer = _posting(target_company_id=company.id)
    jp_invite = _posting(target_company_id=company.id)
    db_session.add_all([jp_offer, jp_invite])
    await db_session.flush()
    for jp in (jp_offer, jp_invite):
        db_session.add(_posting_source(job_posting_id=jp.id))

    db_session.add(_outcome(job_posting_id=jp_offer.id, outcome_type="offer"))
    db_session.add(_outcome(job_posting_id=jp_invite.id, outcome_type="phone_interview_invite"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=rejected")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert resp.json()["items"] == []


# NOTE: ``test_rejected_ignores_company_scoped_events`` was removed in
# feat/surface-linked-outcomes. Its docstring asserted "Rejection is
# per-posting, not per-company" — the inverse of the new contract,
# which intentionally surfaces company-linked rejections because the
# per-posting ``job_posting_id`` was deferred-by-design and uniformly
# NULL in production. NULL-safety on the posting side (``target_company_id
# IS NULL`` must not match) is now covered by
# ``test_rejected_safe_against_null_target_company_id`` in
# ``test_surface_linked_outcomes.py``.


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_surfaces_manual_status_once(db_session: Any) -> None:
    """A posting manually statused 'rejected' surfaces exactly once in
    /rejected — even when its company also has (now hint-only) rejection
    events. Membership is the posting-specific manual status; the company
    Gmail rejections do not add or multiply rows.
    """
    company = _company("Co")
    db_session.add(company)
    await db_session.flush()

    jp = _posting(target_company_id=company.id)
    db_session.add(jp)
    await db_session.flush()
    db_session.add(_posting_source(job_posting_id=jp.id))
    # Company-level rejection events — hint only, must not themselves surface it.
    ev1 = _outcome(job_posting_id=None, outcome_type="rejection_pre_screen")
    ev1.target_company_id = company.id
    ev2 = _outcome(job_posting_id=None, outcome_type="rejection_post_interview")
    ev2.target_company_id = company.id
    db_session.add_all([ev1, ev2])
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            # Posting-specific manual rejected is the authoritative mover.
            put = await ac.put(f"/postings/{jp.id}/status", json={"status": "rejected"})
            assert put.status_code == 200, put.text
            resp = await ac.get("/postings?state=rejected")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(jp.id)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_preserves_two_query_budget(db_session: Any) -> None:
    """``state=rejected`` still emits ≤2 SQL statements (COUNT + SELECT).
    EXISTS folds into the WHERE clause without adding a join."""
    company = _company("Co")
    db_session.add(company)
    await db_session.flush()

    for _ in range(3):
        jp = _posting(target_company_id=company.id)
        db_session.add(jp)
        await db_session.flush()
        db_session.add(_posting_source(job_posting_id=jp.id))
        ev = _outcome(job_posting_id=None, outcome_type="rejection_pre_screen")
        ev.target_company_id = company.id
        db_session.add(ev)
    await db_session.commit()

    counter = _ExecuteCounter(db_session)
    ac = await _client(db_session)
    try:
        async with ac, counter:
            resp = await ac.get("/postings?state=rejected&limit=100")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert counter.count <= 2, f"state=rejected emitted {counter.count} queries (expected ≤2)"
