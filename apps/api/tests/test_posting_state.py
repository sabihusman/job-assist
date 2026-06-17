"""Tests for PR #31 — operator triage actions.

Covers:
  POST /postings/{id}/state            — write path + cross-field validation
  GET  /postings  (state extension)    — list shape, state filter, snoozed flag
  GET  /postings/{id} (state extension)— detail shape + state_history
  services/posting_actions             — bulk N+1 contract

Layout mirrors test_read_endpoints.py — shared ASGI client builder, shared
``_ExecuteCounter`` wrapper, factory functions.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import ApplicationResume, JobPosting, PostingAction, TargetCompany

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
    """Wraps ``session.execute`` to count statements (no-N+1 contract).

    Sync + async context-manager protocols, same as PR #30a's tests.
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


def _company(name: str = "ActionCo", tier: int = 1) -> TargetCompany:
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
        remote_type="remote",
        role_family="product_management",
        seniority_level="senior_pm",
        jd_text="JD.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
    )


def _action(
    *,
    job_posting_id: uuid.UUID,
    action_type: str,
    reason: str | None = None,
    snooze_until: datetime | None = None,
    created_at: datetime | None = None,
) -> PostingAction:
    return PostingAction(
        job_posting_id=job_posting_id,
        action_type=action_type,
        reason=reason,
        snooze_until=snooze_until,
        created_at=created_at or datetime.now(tz=UTC),
    )


def _future(seconds: int = 3600) -> datetime:
    return datetime.now(tz=UTC) + timedelta(seconds=seconds)


def _past(seconds: int = 3600) -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=seconds)


# ── Setup helper ─────────────────────────────────────────────────────────────


async def _make_posting(db_session: Any) -> uuid.UUID:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id)
    db_session.add(posting)
    await db_session.commit()
    return posting.id


# ── POST /postings/{id}/state — sync validation ──────────────────────────────


@_NEEDS_DB
async def test_post_state_missing_reason_when_not_interested(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "not_interested"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422
    assert "reason" in resp.text.lower()


@_NEEDS_DB
async def test_post_state_reason_set_when_not_required(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "interested", "reason": "wrong_role"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422
    assert "reason" in resp.text.lower()


@_NEEDS_DB
async def test_post_state_snooze_until_in_past(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={
                    "action_type": "snoozed",
                    "snooze_until": _past().isoformat(),
                },
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422
    assert "future" in resp.text.lower()


@_NEEDS_DB
async def test_post_state_snooze_until_without_snoozed_action(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={
                    "action_type": "interested",
                    "snooze_until": _future().isoformat(),
                },
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422
    assert "snooze_until" in resp.text.lower()


@_NEEDS_DB
async def test_post_state_unknown_action_type(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "considered"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
async def test_post_state_unknown_reason(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={
                    "action_type": "not_interested",
                    "reason": "vibes_off",
                },
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422


# ── POST /postings/{id}/state — DB-backed happy paths ────────────────────────


@_NEEDS_DB
async def test_post_state_interested_happy_path(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "interested"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current"] == "interested"
    assert body["reason"] is None
    assert body["snooze_until"] is None
    assert body["current_at"] is not None


@_NEEDS_DB
async def test_post_state_not_interested_with_reason(db_session: Any) -> None:
    from sqlalchemy import select

    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "not_interested", "reason": "comp_too_low"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    assert resp.json()["reason"] == "comp_too_low"
    # Row persisted.
    rows = (
        (
            await db_session.execute(
                select(PostingAction).where(PostingAction.job_posting_id == posting_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].action_type == "not_interested"
    assert rows[0].reason == "comp_too_low"


@_NEEDS_DB
async def test_post_state_applied(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "applied"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200
    assert resp.json()["current"] == "applied"


# ── feat/triple-aware-apply (1b): resume_attached signal ─────────────────────


@_NEEDS_DB
async def test_post_state_applied_resume_attached_false_without_resume(
    db_session: Any,
) -> None:
    """Applying with NO application_resume succeeds (warn-but-allow) and the
    response reports resume_attached=false — never a 4xx."""
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "applied"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current"] == "applied"
    assert body["resume_attached"] is False


@_NEEDS_DB
async def test_post_state_applied_resume_attached_true_with_resume(
    db_session: Any,
) -> None:
    """When an application_resume exists for the posting, an applied action
    reports resume_attached=true (the corpus link is the shared job_posting_id)."""
    posting_id = await _make_posting(db_session)
    db_session.add(
        ApplicationResume(job_posting_id=posting_id, file_name="r.docx", resume_text="hi")
    )
    await db_session.commit()
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "applied"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    assert resp.json()["resume_attached"] is True


@_NEEDS_DB
async def test_post_state_non_applied_resume_attached_null(db_session: Any) -> None:
    """resume_attached is null for non-applied actions even when a resume
    exists — the field is only meaningful for an applied action."""
    posting_id = await _make_posting(db_session)
    db_session.add(ApplicationResume(job_posting_id=posting_id, file_name="r.docx"))
    await db_session.commit()
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "interested"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    assert resp.json()["resume_attached"] is None


@_NEEDS_DB
async def test_post_state_snoozed_with_future_snooze_until(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    until = _future(86400)  # 1 day out
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={
                    "action_type": "snoozed",
                    "snooze_until": until.isoformat(),
                },
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == "snoozed"
    assert body["snooze_until"] is not None


@_NEEDS_DB
async def test_post_state_snoozed_without_snooze_until(db_session: Any) -> None:
    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "snoozed"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == "snoozed"
    assert body["snooze_until"] is None


@_NEEDS_DB
async def test_post_state_reset(db_session: Any) -> None:
    from job_assist.db.enums import ActionType
    from job_assist.services.posting_actions import get_current_state

    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "interested"},
            )
            resp = await ac.post(
                f"/postings/{posting_id}/state",
                json={"action_type": "reset"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 200
    assert resp.json()["current"] == "reset"
    current = await get_current_state(db_session, posting_id)
    assert current is not None
    assert current.action_type == ActionType.reset


@_NEEDS_DB
async def test_post_state_404_unknown_posting(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/postings/{uuid.uuid4()}/state",
                json={"action_type": "interested"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 404


@_NEEDS_DB
async def test_post_state_append_only(db_session: Any) -> None:
    from sqlalchemy import select

    posting_id = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            for action in ("interested", "applied", "reset"):
                resp = await ac.post(
                    f"/postings/{posting_id}/state",
                    json={"action_type": action},
                )
                assert resp.status_code == 200, resp.text
    finally:
        await _drop_override()

    rows = (
        (
            await db_session.execute(
                select(PostingAction)
                .where(PostingAction.job_posting_id == posting_id)
                .order_by(PostingAction.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [r.action_type for r in rows] == ["interested", "applied", "reset"]


# ── DB CHECK constraints (direct INSERT, bypassing service) ──────────────────


@_NEEDS_DB
async def test_db_check_constraint_blocks_reason_without_not_interested(
    db_session: Any,
) -> None:
    from sqlalchemy.exc import IntegrityError

    posting_id = await _make_posting(db_session)
    bad = _action(
        job_posting_id=posting_id,
        action_type="interested",
        reason="wrong_role",
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@_NEEDS_DB
async def test_db_check_constraint_requires_reason_for_not_interested(
    db_session: Any,
) -> None:
    from sqlalchemy.exc import IntegrityError

    posting_id = await _make_posting(db_session)
    bad = _action(job_posting_id=posting_id, action_type="not_interested", reason=None)
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# ── GET /postings — state in response & state filter ────────────────────────


@_NEEDS_DB
async def test_get_postings_state_in_response(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p1 = _posting(target_company_id=company.id)
    p2 = _posting(target_company_id=company.id)
    db_session.add_all([p1, p2])
    await db_session.flush()
    db_session.add(_action(job_posting_id=p1.id, action_type="interested"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    items = {item["id"]: item for item in resp.json()["items"]}
    assert items[str(p1.id)]["state"]["current"] == "interested"
    assert items[str(p2.id)]["state"]["current"] is None


@_NEEDS_DB
async def test_get_postings_state_triage_filter(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    # p_untouched: no action → triage
    p_untouched = _posting(target_company_id=company.id)
    # p_reset: latest action is reset → also triage
    p_reset = _posting(target_company_id=company.id)
    # p_interested: latest action interested → NOT triage
    p_interested = _posting(target_company_id=company.id)
    db_session.add_all([p_untouched, p_reset, p_interested])
    await db_session.flush()
    now = datetime.now(tz=UTC)
    db_session.add(
        _action(
            job_posting_id=p_reset.id,
            action_type="interested",
            created_at=now - timedelta(seconds=10),
        )
    )
    db_session.add(_action(job_posting_id=p_reset.id, action_type="reset", created_at=now))
    db_session.add(_action(job_posting_id=p_interested.id, action_type="interested"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=triage")
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {str(p_untouched.id), str(p_reset.id)}


@_NEEDS_DB
async def test_get_postings_state_filter_interested(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p_int = _posting(target_company_id=company.id)
    p_app = _posting(target_company_id=company.id)
    db_session.add_all([p_int, p_app])
    await db_session.flush()
    db_session.add(_action(job_posting_id=p_int.id, action_type="interested"))
    db_session.add(_action(job_posting_id=p_app.id, action_type="applied"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=interested")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {str(p_int.id)}


@_NEEDS_DB
async def test_get_postings_state_filter_multi(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p_int = _posting(target_company_id=company.id)
    p_app = _posting(target_company_id=company.id)
    p_ni = _posting(target_company_id=company.id)
    db_session.add_all([p_int, p_app, p_ni])
    await db_session.flush()
    db_session.add(_action(job_posting_id=p_int.id, action_type="interested"))
    db_session.add(_action(job_posting_id=p_app.id, action_type="applied"))
    db_session.add(
        _action(
            job_posting_id=p_ni.id,
            action_type="not_interested",
            reason="wrong_role",
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=interested&state=applied")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {str(p_int.id), str(p_app.id)}


@_NEEDS_DB
async def test_get_postings_snoozed_past_only_filter(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p_future = _posting(target_company_id=company.id)
    p_past = _posting(target_company_id=company.id)
    db_session.add_all([p_future, p_past])
    await db_session.flush()
    db_session.add(
        _action(
            job_posting_id=p_future.id,
            action_type="snoozed",
            snooze_until=_future(86400),
        )
    )
    db_session.add(
        _action(
            job_posting_id=p_past.id,
            action_type="snoozed",
            snooze_until=_past(3600),
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=snoozed&include_snoozed_past_only=true")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {str(p_past.id)}


@_NEEDS_DB
async def test_get_postings_snoozed_open_past_7d(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    p_old_open = _posting(target_company_id=company.id)
    p_recent_open = _posting(target_company_id=company.id)
    db_session.add_all([p_old_open, p_recent_open])
    await db_session.flush()
    eight_days_ago = datetime.now(tz=UTC) - timedelta(days=8)
    db_session.add(
        _action(
            job_posting_id=p_old_open.id,
            action_type="snoozed",
            snooze_until=None,
            created_at=eight_days_ago,
        )
    )
    db_session.add(
        _action(
            job_posting_id=p_recent_open.id,
            action_type="snoozed",
            snooze_until=None,
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/postings?state=snoozed&include_snoozed_past_only=true")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {str(p_old_open.id)}


# ── GET /postings/{id} — detail with state + history ────────────────────────


@_NEEDS_DB
async def test_get_posting_detail_state_and_history(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id)
    db_session.add(posting)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    db_session.add(
        _action(
            job_posting_id=posting.id,
            action_type="interested",
            created_at=now - timedelta(minutes=20),
        )
    )
    db_session.add(
        _action(
            job_posting_id=posting.id,
            action_type="applied",
            created_at=now - timedelta(minutes=10),
        )
    )
    db_session.add(
        _action(
            job_posting_id=posting.id,
            action_type="reset",
            created_at=now,
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings/{posting.id}")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["current"] == "reset"
    history = body["state_history"]
    assert [h["action_type"] for h in history] == ["interested", "applied", "reset"]


@_NEEDS_DB
async def test_get_posting_detail_state_history_empty_when_no_actions(
    db_session: Any,
) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    posting = _posting(target_company_id=company.id)
    db_session.add(posting)
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/postings/{posting.id}")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["current"] is None
    assert body["state_history"] == []


# ── Service-level bulk N+1 + endpoint query budget ──────────────────────────


@_NEEDS_DB
async def test_bulk_get_current_states_no_n_plus_one(db_session: Any) -> None:
    from job_assist.services.posting_actions import bulk_get_current_states

    company = _company()
    db_session.add(company)
    await db_session.flush()
    postings = [_posting(target_company_id=company.id) for _ in range(10)]
    db_session.add_all(postings)
    await db_session.flush()

    now = datetime.now(tz=UTC)
    # Seed varying action counts across the 10 postings.
    for i, p in enumerate(postings):
        for k in range(i % 3):
            db_session.add(
                _action(
                    job_posting_id=p.id,
                    action_type="interested",
                    created_at=now - timedelta(minutes=10 - k),
                )
            )
    await db_session.commit()

    ids = [p.id for p in postings]
    async with _ExecuteCounter(db_session) as counter:
        result = await bulk_get_current_states(db_session, ids)
    assert counter.count == 1
    assert set(result.keys()) == set(ids)


@_NEEDS_DB
async def test_get_postings_total_query_budget(db_session: Any) -> None:
    company = _company()
    db_session.add(company)
    await db_session.flush()
    for _ in range(5):
        p = _posting(target_company_id=company.id)
        db_session.add(p)
        await db_session.flush()
        db_session.add(_action(job_posting_id=p.id, action_type="interested"))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with _ExecuteCounter(db_session) as counter, ac:
            resp = await ac.get("/postings")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    # Folded state LATERAL keeps GET /postings at the PR #30a budget of 2.
    assert counter.count <= 2, f"GET /postings issued {counter.count} queries"
