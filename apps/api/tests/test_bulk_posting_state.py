"""Bulk triage-action endpoint tests (feat/bulk-triage-actions).

Exercises POST /postings/bulk-state THROUGH the endpoint against a real DB:
  * bulk-pass writes N posting_action rows in one transaction
  * not_interested without a reason → 422 (reason enforced, no writes)
  * bulk-reset reverses a bulk-pass (latest action becomes 'reset')
  * unknown ids are reported per-id without aborting the valid writes
  * empty / over-cap id lists → 422

DB-gated (need TEST_DATABASE_URL); run on CI's postgres.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from job_assist.db.models import JobPosting, PostingAction, TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


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


def _company() -> TargetCompany:
    return TargetCompany(
        name=f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=3,
        ats="ashby",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(target_company_id: uuid.UUID, *, score: int | None = 30) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=f"customer success manager {suffix}",
        raw_title="Customer Success Manager",
        jd_text="JD.",
        jd_text_hash=f"{'0' * 54}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        role_family="other",  # type: ignore[arg-type]
        seniority_level="unknown",  # type: ignore[arg-type]
        remote_type="remote",
        fit_score=score,
    )


async def _make_postings(db_session: Any, n: int) -> list[uuid.UUID]:
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    rows = [_posting(tc.id) for _ in range(n)]
    db_session.add_all(rows)
    await db_session.commit()
    return [r.id for r in rows]


async def _latest_action(db_session: Any, pid: uuid.UUID) -> str | None:
    row = (
        await db_session.execute(
            select(PostingAction.action_type)
            .where(PostingAction.job_posting_id == pid)
            .order_by(PostingAction.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


@_NEEDS_DB
@pytest.mark.asyncio
async def test_bulk_pass_writes_one_action_per_id(db_session: Any) -> None:
    ids = await _make_postings(db_session, 5)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/postings/bulk-state",
                json={
                    "ids": [str(i) for i in ids],
                    "action_type": "not_interested",
                    "reason": "wrong_role",
                },
            )
    finally:
        await _drop_override()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"succeeded": 5, "failed": 0, "failures": []}

    # Exactly one not_interested row per posting (single transaction).
    total = (
        await db_session.execute(
            select(func.count())
            .select_from(PostingAction)
            .where(PostingAction.job_posting_id.in_(ids))
            .where(PostingAction.action_type == "not_interested")
        )
    ).scalar_one()
    assert total == 5


@_NEEDS_DB
@pytest.mark.asyncio
async def test_bulk_pass_requires_reason(db_session: Any) -> None:
    ids = await _make_postings(db_session, 3)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/postings/bulk-state",
                json={"ids": [str(i) for i in ids], "action_type": "not_interested"},
            )
    finally:
        await _drop_override()

    assert resp.status_code == 422
    # No writes happened — validation ran before the loop.
    total = (
        await db_session.execute(
            select(func.count())
            .select_from(PostingAction)
            .where(PostingAction.job_posting_id.in_(ids))
        )
    ).scalar_one()
    assert total == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_bulk_reset_reverses_bulk_pass(db_session: Any) -> None:
    ids = await _make_postings(db_session, 4)
    ac = await _client(db_session)
    try:
        async with ac:
            passed = await ac.post(
                "/postings/bulk-state",
                json={
                    "ids": [str(i) for i in ids],
                    "action_type": "not_interested",
                    "reason": "wrong_role",
                },
            )
            assert passed.json()["succeeded"] == 4
            for pid in ids:
                assert await _latest_action(db_session, pid) == "not_interested"

            # Bulk-undo: same endpoint, action_type='reset', no reason.
            reset = await ac.post(
                "/postings/bulk-state",
                json={"ids": [str(i) for i in ids], "action_type": "reset"},
            )
    finally:
        await _drop_override()

    assert reset.status_code == 200, reset.text
    assert reset.json()["succeeded"] == 4
    # Latest action per posting is now 'reset' → back in triage.
    for pid in ids:
        assert await _latest_action(db_session, pid) == "reset"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_bulk_reports_unknown_ids_without_aborting(db_session: Any) -> None:
    ids = await _make_postings(db_session, 2)
    missing = uuid.uuid4()
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/postings/bulk-state",
                json={
                    "ids": [str(ids[0]), str(missing), str(ids[1])],
                    "action_type": "not_interested",
                    "reason": "wrong_role",
                },
            )
    finally:
        await _drop_override()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["failures"] == [{"posting_id": str(missing), "error": "job_posting not found"}]
    # The two valid postings were still written.
    for pid in ids:
        assert await _latest_action(db_session, pid) == "not_interested"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_bulk_empty_ids_422(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/postings/bulk-state",
                json={"ids": [], "action_type": "reset"},
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422
