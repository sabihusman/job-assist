"""Endpoint tests for outreach_message (PR #52).

Covers ``POST /contacts/{contact_id}/outreach``,
``GET /contacts/{contact_id}/outreach``, ``GET /outreach/recent``.

Also asserts the 2-query budget on both list endpoints via the
``_ExecuteCounter`` pattern established in PR #30a.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from job_assist.db.models import Contact, JobPosting, OutreachMessage, PostingSource

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


class _ExecuteCounter:
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


async def _clear(db_session: Any) -> None:
    await db_session.execute(delete(OutreachMessage))
    await db_session.execute(delete(PostingSource))
    await db_session.execute(delete(JobPosting))
    await db_session.execute(delete(Contact))
    await db_session.commit()


async def _make_contact(db_session: Any, *, n: int = 1) -> uuid.UUID:
    c = Contact(
        first_name=f"Test{n}",
        last_name=f"Person{n}",
        email_primary=f"t{n}-{uuid.uuid4().hex[:6]}@example.test",
        source_type="linkedin_outreach",
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c.id


def _basic_outreach_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "direction": "outbound",
        "channel": "linkedin",
        "sent_at": "2026-06-01T12:00:00Z",
        "subject": "Hello",
        "body": "Test message",
    }
    base.update(overrides)
    return base


# ── POST /contacts/{contact_id}/outreach ────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_forces_source_manual(db_session: Any) -> None:
    """Server forces ``source='manual'`` regardless of client input."""
    await _clear(db_session)
    cid = await _make_contact(db_session)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/contacts/{cid}/outreach",
                json=_basic_outreach_payload(),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "manual"
    assert body["external_message_id"] is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_rejects_source_in_body(db_session: Any) -> None:
    """``extra='forbid'`` — passing ``source`` 422s."""
    await _clear(db_session)
    cid = await _make_contact(db_session)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/contacts/{cid}/outreach",
                json=_basic_outreach_payload(source="gmail_auto"),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_rejects_unknown_direction(db_session: Any) -> None:
    await _clear(db_session)
    cid = await _make_contact(db_session)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/contacts/{cid}/outreach",
                json=_basic_outreach_payload(direction="diagonal"),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_unknown_contact_404(db_session: Any) -> None:
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/contacts/{uuid.uuid4()}/outreach",
                json=_basic_outreach_payload(),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 404


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_unknown_posting_id_404(db_session: Any) -> None:
    """Pre-check + 404 mirrors PR #31's posting_action precedent."""
    await _clear(db_session)
    cid = await _make_contact(db_session)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/contacts/{cid}/outreach",
                json=_basic_outreach_payload(posting_id=str(uuid.uuid4())),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 404


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_with_real_posting_id(db_session: Any) -> None:
    """Linking to a real posting succeeds and the FK persists."""
    await _clear(db_session)
    cid = await _make_contact(db_session)

    jp = JobPosting(
        canonical_company_name="TestCo",
        normalized_title="senior pm",
        raw_title="Senior PM",
        remote_type="remote",
        role_family="product_management",
        jd_text="JD body.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{uuid.uuid4().hex[:8]}",
        first_seen_at=datetime.now(tz=UTC),
        last_seen_at=datetime.now(tz=UTC),
    )
    db_session.add(jp)
    await db_session.commit()
    await db_session.refresh(jp)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                f"/contacts/{cid}/outreach",
                json=_basic_outreach_payload(posting_id=str(jp.id)),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 201
    assert resp.json()["posting_id"] == str(jp.id)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_log_outreach_against_archived_contact_succeeds(db_session: Any) -> None:
    """Archive is "stop initiating" — inbound replies must still log."""
    await _clear(db_session)
    cid = await _make_contact(db_session)

    ac = await _client(db_session)
    try:
        async with ac:
            await ac.post(f"/contacts/{cid}/archive")
            resp = await ac.post(
                f"/contacts/{cid}/outreach",
                json=_basic_outreach_payload(direction="inbound"),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 201


# ── GET /contacts/{contact_id}/outreach ─────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_outreach_newest_first_with_id_tiebreaker(db_session: Any) -> None:
    """Order: ``sent_at DESC, id ASC``. Same-timestamp rows sort by id ASC."""
    await _clear(db_session)
    cid = await _make_contact(db_session)

    # Three messages with the same sent_at — id ASC must break the tie.
    same_ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    rows = [
        OutreachMessage(
            contact_id=cid,
            direction="outbound",
            channel="linkedin",
            source="manual",
            sent_at=same_ts,
            subject=f"msg {i}",
        )
        for i in range(3)
    ]
    # Plus one newer.
    rows.append(
        OutreachMessage(
            contact_id=cid,
            direction="outbound",
            channel="email",
            source="manual",
            sent_at=same_ts + timedelta(hours=1),
            subject="newest",
        )
    )
    for r in rows:
        db_session.add(r)
    await db_session.commit()
    for r in rows:
        await db_session.refresh(r)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/contacts/{cid}/outreach")
    finally:
        await _drop_override()

    items = resp.json()["items"]
    assert len(items) == 4
    assert items[0]["subject"] == "newest"  # newest sent_at first

    # The three same-timestamp rows must appear in id ASC order.
    tied_ids = [item["id"] for item in items[1:]]
    same_ts_ids = sorted(str(r.id) for r in rows[:3])
    assert tied_ids == same_ts_ids


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_outreach_unknown_contact_404(db_session: Any) -> None:
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/contacts/{uuid.uuid4()}/outreach")
    finally:
        await _drop_override()
    assert resp.status_code == 404


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_outreach_two_query_budget(db_session: Any) -> None:
    """Endpoint emits exactly 2 SQL statements after contact pre-check.

    The contact pre-check (1 SELECT) + COUNT (1) + SELECT (1) = 3
    statements total. We use the counter only after that pre-check
    to lock the budget on the data-bearing queries.
    """
    await _clear(db_session)
    cid = await _make_contact(db_session)
    for i in range(5):
        db_session.add(
            OutreachMessage(
                contact_id=cid,
                direction="outbound",
                channel="linkedin",
                source="manual",
                sent_at=datetime.now(tz=UTC),
                subject=f"msg {i}",
            )
        )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            with _ExecuteCounter(db_session) as counter:
                resp = await ac.get(f"/contacts/{cid}/outreach")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    # Contact pre-check + COUNT + SELECT = 3.
    assert counter.count == 3


# ── GET /outreach/recent ────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_outreach_recent_includes_contact_summary(db_session: Any) -> None:
    """Cross-contact feed joins minimal contact info inline."""
    await _clear(db_session)
    c1 = await _make_contact(db_session, n=1)
    c2 = await _make_contact(db_session, n=2)
    db_session.add(
        OutreachMessage(
            contact_id=c1,
            direction="outbound",
            channel="linkedin",
            source="manual",
            sent_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )
    db_session.add(
        OutreachMessage(
            contact_id=c2,
            direction="inbound",
            channel="email",
            source="manual",
            sent_at=datetime(2026, 6, 2, tzinfo=UTC),
        )
    )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/outreach/recent")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    # Newest first.
    assert body["items"][0]["contact_id"] == str(c2)
    assert body["items"][0]["contact_first_name"] == "Test2"
    assert body["items"][0]["contact_source_type"] == "linkedin_outreach"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_outreach_recent_two_query_budget(db_session: Any) -> None:
    """``GET /outreach/recent`` is exactly 2 statements (COUNT + SELECT-JOIN)."""
    await _clear(db_session)
    c1 = await _make_contact(db_session, n=1)
    for i in range(3):
        db_session.add(
            OutreachMessage(
                contact_id=c1,
                direction="outbound",
                channel="linkedin",
                source="manual",
                sent_at=datetime.now(tz=UTC),
                subject=f"m {i}",
            )
        )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            with _ExecuteCounter(db_session) as counter:
                resp = await ac.get("/outreach/recent")
    finally:
        await _drop_override()
    assert resp.status_code == 200
    assert counter.count == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_outreach_recent_pagination(db_session: Any) -> None:
    await _clear(db_session)
    cid = await _make_contact(db_session)
    base = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(5):
        db_session.add(
            OutreachMessage(
                contact_id=cid,
                direction="outbound",
                channel="linkedin",
                source="manual",
                sent_at=base + timedelta(minutes=i),
                subject=f"m {i}",
            )
        )
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/outreach/recent?limit=2&offset=0")
    finally:
        await _drop_override()
    body = resp.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert len(body["items"]) == 2
    # Newest first — index 4 then 3.
    assert body["items"][0]["subject"] == "m 4"
    assert body["items"][1]["subject"] == "m 3"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_outreach_recent_limit_validation(db_session: Any) -> None:
    """``limit=0`` → 422, ``limit=101`` → 422."""
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.get("/outreach/recent?limit=0")
            r2 = await ac.get("/outreach/recent?limit=101")
    finally:
        await _drop_override()
    assert r1.status_code == 422
    assert r2.status_code == 422
