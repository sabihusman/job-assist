"""Endpoint tests for contact CRUD (PR #52).

Covers ``POST /contacts``, ``GET /contacts/{id}``,
``PATCH /contacts/{id}``, ``POST /contacts/{id}/archive``,
``POST /contacts/{id}/unarchive``.

PII discipline: every contact uses fake names like ``TestOperator``
and ``test@example.test``. No real PII.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from job_assist.db.models import Contact, OutreachMessage

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


async def _clear(db_session: Any) -> None:
    await db_session.execute(delete(OutreachMessage))
    await db_session.execute(delete(Contact))
    await db_session.commit()


def _seed_payload(**overrides: Any) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:6]
    base: dict[str, Any] = {
        "first_name": "TestOperator",
        "last_name": "Person",
        "email_primary": f"test-{suffix}@example.test",
        "source_type": "linkedin_outreach",
    }
    base.update(overrides)
    return base


# ── POST /contacts ──────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_contact_returns_full_detail(db_session: Any) -> None:
    """``POST /contacts`` returns the full ContactDetail shape."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/contacts",
                json=_seed_payload(notes="Met at conf"),
            )
    finally:
        await _drop_override()

    assert resp.status_code == 201
    body = resp.json()
    assert body["first_name"] == "TestOperator"
    assert body["source_type"] == "linkedin_outreach"
    assert body["notes"] == "Met at conf"
    assert body["archived_at"] is None
    assert body["phone"] is None
    assert "updated_at" in body  # detail-only field


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_contact_rejects_missing_channel(db_session: Any) -> None:
    """``ContactCreate`` requires at least one of email or linkedin_url."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/contacts",
                json={
                    "first_name": "TestNoChannel",
                    "last_name": "Person",
                    "source_type": "warm_intro",
                },
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_contact_rejects_unknown_source_type(db_session: Any) -> None:
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post("/contacts", json=_seed_payload(source_type="bogus"))
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_contact_rejects_extra_fields(db_session: Any) -> None:
    """``extra='forbid'`` — passing ``source`` or anything unknown 422s."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/contacts",
                json=_seed_payload(unexpected_field="surprise"),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_contact_email_uniqueness_returns_422(db_session: Any) -> None:
    """Active-row email collision returns 422 with a clean message."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            email = "shared-uniq@example.test"
            r1 = await ac.post("/contacts", json=_seed_payload(email_primary=email))
            assert r1.status_code == 201
            r2 = await ac.post(
                "/contacts",
                json=_seed_payload(
                    email_primary=email,
                    last_name="Different",
                ),
            )
    finally:
        await _drop_override()
    assert r2.status_code == 422
    assert "email_primary" in r2.json()["detail"]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_contact_unknown_target_company_404(db_session: Any) -> None:
    await _clear(db_session)
    bogus = str(uuid.uuid4())
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(
                "/contacts",
                json=_seed_payload(target_company_id=bogus),
            )
    finally:
        await _drop_override()
    assert resp.status_code == 404


# ── GET /contacts/{id} ──────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_get_contact_returns_detail(db_session: Any) -> None:
    """``GET /contacts/{id}`` returns the full detail shape."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            r2 = await ac.get(f"/contacts/{cid}")
    finally:
        await _drop_override()
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == cid
    assert body["source_type"] == "linkedin_outreach"
    # The 8 detail-only fields are present.
    for key in (
        "phone",
        "notes",
        "contact_opt_in",
        "contact_opt_in_topics",
        "source_metadata",
        "job_functions_of_interest",
        "industries_of_interest",
        "updated_at",
    ):
        assert key in body


@_NEEDS_DB
@pytest.mark.asyncio
async def test_get_contact_unknown_id_404(db_session: Any) -> None:
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get(f"/contacts/{uuid.uuid4()}")
    finally:
        await _drop_override()
    assert resp.status_code == 404


@_NEEDS_DB
@pytest.mark.asyncio
async def test_get_contact_works_for_archived(db_session: Any) -> None:
    """Detail endpoint returns archived contacts (operator can unarchive)."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            await ac.post(f"/contacts/{cid}/archive")
            r2 = await ac.get(f"/contacts/{cid}")
    finally:
        await _drop_override()
    assert r2.status_code == 200
    assert r2.json()["archived_at"] is not None


# ── PATCH /contacts/{id} ────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_patch_updates_mutable_fields(db_session: Any) -> None:
    """Update notes + opt-in via PATCH."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            r2 = await ac.patch(
                f"/contacts/{cid}",
                json={"notes": "Updated", "contact_opt_in": True, "phone": "+1-555-0100"},
            )
    finally:
        await _drop_override()
    assert r2.status_code == 200
    body = r2.json()
    assert body["notes"] == "Updated"
    assert body["contact_opt_in"] is True
    assert body["phone"] == "+1-555-0100"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_patch_rejects_immutable_fields(db_session: Any) -> None:
    """``first_name`` / ``last_name`` / ``source_type`` are rejected."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            r2 = await ac.patch(f"/contacts/{cid}", json={"first_name": "NewName"})
    finally:
        await _drop_override()
    assert r2.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_patch_omitted_keys_dont_clear_values(db_session: Any) -> None:
    """Sending ``{"notes": "x"}`` doesn't null other fields.

    Locks the ``exclude_unset=True`` semantic — present keys (even
    ``null``) are applied; absent keys are left alone.
    """
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post(
                "/contacts",
                json=_seed_payload(notes="Original", current_position="PM"),
            )
            cid = r1.json()["id"]
            r2 = await ac.patch(f"/contacts/{cid}", json={"notes": "Updated"})
    finally:
        await _drop_override()
    body = r2.json()
    assert body["notes"] == "Updated"
    assert body["current_position"] == "PM"  # untouched


@_NEEDS_DB
@pytest.mark.asyncio
async def test_patch_explicit_null_clears_field(db_session: Any) -> None:
    """Sending ``{"notes": null}`` clears the value."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload(notes="To clear"))
            cid = r1.json()["id"]
            r2 = await ac.patch(f"/contacts/{cid}", json={"notes": None})
    finally:
        await _drop_override()
    assert r2.json()["notes"] is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_patch_clearing_both_channels_422(db_session: Any) -> None:
    """Reachability is re-asserted: clearing email + linkedin → 422."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post(
                "/contacts",
                json=_seed_payload(linkedin_url="https://linkedin.com/in/test-x"),
            )
            cid = r1.json()["id"]
            # Try to clear both — must 422.
            r2 = await ac.patch(
                f"/contacts/{cid}",
                json={"email_primary": None, "linkedin_url": None},
            )
    finally:
        await _drop_override()
    assert r2.status_code == 422
    assert "email_primary" in r2.json()["detail"] or "linkedin_url" in r2.json()["detail"]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_patch_unknown_id_404(db_session: Any) -> None:
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.patch(f"/contacts/{uuid.uuid4()}", json={"notes": "x"})
    finally:
        await _drop_override()
    assert resp.status_code == 404


# ── archive / unarchive ─────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_archive_sets_archived_at(db_session: Any) -> None:
    """``POST /contacts/{id}/archive`` sets archived_at; row excluded from list."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            r2 = await ac.post(f"/contacts/{cid}/archive")
            assert r2.status_code == 204

            # Default list excludes archived.
            r3 = await ac.get("/contacts")
            assert all(item["id"] != cid for item in r3.json()["items"])

            # include_archived=true brings it back.
            r4 = await ac.get("/contacts?include_archived=true")
            assert any(item["id"] == cid for item in r4.json()["items"])
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_archive_is_idempotent(db_session: Any) -> None:
    """Archiving twice doesn't 500."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            r2 = await ac.post(f"/contacts/{cid}/archive")
            r3 = await ac.post(f"/contacts/{cid}/archive")
    finally:
        await _drop_override()
    assert r2.status_code == 204
    assert r3.status_code == 204


@_NEEDS_DB
@pytest.mark.asyncio
async def test_unarchive_restores_row(db_session: Any) -> None:
    """``POST /contacts/{id}/unarchive`` clears archived_at and row returns."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            await ac.post(f"/contacts/{cid}/archive")
            r2 = await ac.post(f"/contacts/{cid}/unarchive")
            assert r2.status_code == 204
            r3 = await ac.get("/contacts")
            assert any(item["id"] == cid for item in r3.json()["items"])
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_archive_preserves_outreach_history(db_session: Any) -> None:
    """Archiving doesn't cascade-delete outreach messages."""
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.post("/contacts", json=_seed_payload())
            cid = r1.json()["id"]
            await ac.post(
                f"/contacts/{cid}/outreach",
                json={
                    "direction": "outbound",
                    "channel": "linkedin",
                    "sent_at": "2026-06-01T12:00:00Z",
                    "body": "hello",
                },
            )
            await ac.post(f"/contacts/{cid}/archive")

            # Outreach row still exists in the DB.
            count = (
                (
                    await db_session.execute(
                        select(OutreachMessage).where(OutreachMessage.contact_id == uuid.UUID(cid))
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await _drop_override()
    assert len(count) == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_archive_then_unarchive_collision_422(db_session: Any) -> None:
    """Unarchive returns 422 if another active contact now holds the email.

    Workflow: A active → archive A → create B with A's email (allowed
    because A's row no longer occupies the partial-UNIQUE slot) →
    unarchive A → 422 (B's slot is in the way).
    """
    await _clear(db_session)
    shared_email = "collision-target@example.test"
    ac = await _client(db_session)
    try:
        async with ac:
            r_a = await ac.post("/contacts", json=_seed_payload(email_primary=shared_email))
            a_id = r_a.json()["id"]
            await ac.post(f"/contacts/{a_id}/archive")

            # B gets the slot because A is archived.
            r_b = await ac.post(
                "/contacts",
                json=_seed_payload(email_primary=shared_email, last_name="Beta"),
            )
            assert r_b.status_code == 201

            # Unarchive A — must 422 with a clean message.
            r_unarchive = await ac.post(f"/contacts/{a_id}/unarchive")
    finally:
        await _drop_override()
    assert r_unarchive.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_archive_unknown_id_404(db_session: Any) -> None:
    await _clear(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(f"/contacts/{uuid.uuid4()}/archive")
    finally:
        await _drop_override()
    assert resp.status_code == 404
