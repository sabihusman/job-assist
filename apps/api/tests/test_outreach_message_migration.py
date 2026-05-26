"""Migration tests for ``outreach_message`` (PR #52).

Validates the schema landed correctly by querying ``pg_catalog`` and
exercising the CHECK constraints + partial UNIQUE index from
Python. Same shape as ``test_contact_migration.py``.

Skipped when ``TEST_DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from job_assist.db.models import Contact, OutreachMessage

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _contact(*, n: int = 1) -> Contact:
    suffix = uuid.uuid4().hex[:6]
    return Contact(
        first_name=f"Test{n}",
        last_name=f"Person{n}",
        email_primary=f"test{n}-{suffix}@example.test",
        source_type="tippie_alumni",
    )


def _msg(
    *,
    contact_id: uuid.UUID,
    direction: str = "outbound",
    channel: str = "linkedin",
    source: str = "manual",
    external_message_id: str | None = None,
    sent_at: datetime | None = None,
) -> OutreachMessage:
    return OutreachMessage(
        contact_id=contact_id,
        direction=direction,
        channel=channel,
        source=source,
        external_message_id=external_message_id,
        sent_at=sent_at or datetime.now(tz=UTC),
    )


async def _clear(db_session: Any) -> None:
    await db_session.execute(delete(OutreachMessage))
    await db_session.execute(delete(Contact))
    await db_session.commit()


# ── Schema presence ─────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_table_exists(db_session: Any) -> None:
    """``outreach_message`` is registered in ``pg_class``."""
    res = await db_session.execute(
        text("SELECT 1 FROM pg_class WHERE relname='outreach_message' AND relkind='r'")
    )
    assert res.scalar_one() == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_contact_phone_column_exists(db_session: Any) -> None:
    """The ``contact.phone`` column landed in the same migration."""
    res = await db_session.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='contact' AND column_name='phone'"
        )
    )
    assert res.scalar_one() == "text"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_lookup_index_exists(db_session: Any) -> None:
    """``idx_outreach_message_contact_id_sent_at_desc`` is registered."""
    res = await db_session.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE indexname="
            "'idx_outreach_message_contact_id_sent_at_desc'"
        )
    )
    assert res.scalar_one() == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_external_message_id_unique_partial_index_exists(db_session: Any) -> None:
    """``uq_outreach_message_external_message_id`` is UNIQUE + partial.

    PR #53 will rely on this to dedup Gmail upserts — declaring it
    UNIQUE here avoids a follow-up migration with data cleanup.
    """
    res = await db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname='uq_outreach_message_external_message_id'"
        )
    )
    indexdef = res.scalar_one()
    assert "UNIQUE INDEX" in indexdef
    assert "WHERE" in indexdef
    assert "external_message_id IS NOT NULL" in indexdef


@_NEEDS_DB
@pytest.mark.asyncio
async def test_posting_id_partial_index_exists(db_session: Any) -> None:
    """``idx_outreach_message_posting_id`` is partial on NOT NULL."""
    res = await db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname='idx_outreach_message_posting_id'")
    )
    indexdef = res.scalar_one()
    assert "WHERE" in indexdef
    assert "posting_id IS NOT NULL" in indexdef


# ── CHECK constraint behaviour ──────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_direction_check_rejects_unknown(db_session: Any) -> None:
    """``ck_outreach_message_direction`` rejects strings outside the vocab."""
    await _clear(db_session)
    c = _contact()
    db_session.add(c)
    await db_session.flush()

    db_session.add(_msg(contact_id=c.id, direction="bogus"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_channel_check_rejects_unknown(db_session: Any) -> None:
    """``ck_outreach_message_channel`` rejects strings outside the vocab."""
    await _clear(db_session)
    c = _contact()
    db_session.add(c)
    await db_session.flush()

    db_session.add(_msg(contact_id=c.id, channel="carrier_pigeon"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_source_check_rejects_unknown(db_session: Any) -> None:
    """``ck_outreach_message_source`` rejects strings outside the vocab."""
    await _clear(db_session)
    c = _contact()
    db_session.add(c)
    await db_session.flush()

    db_session.add(_msg(contact_id=c.id, source="hand_courier"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# ── Partial UNIQUE on external_message_id ───────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_external_message_id_unique_enforced(db_session: Any) -> None:
    """Two rows with the SAME ``external_message_id`` collide."""
    await _clear(db_session)
    c = _contact()
    db_session.add(c)
    await db_session.flush()

    db_session.add(_msg(contact_id=c.id, external_message_id="msg-xyz", source="gmail_auto"))
    await db_session.commit()

    db_session.add(_msg(contact_id=c.id, external_message_id="msg-xyz", source="gmail_auto"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_external_message_id_null_does_not_collide(db_session: Any) -> None:
    """The UNIQUE is partial — NULL allows unlimited rows."""
    await _clear(db_session)
    c = _contact()
    db_session.add(c)
    await db_session.flush()

    for _ in range(3):
        db_session.add(_msg(contact_id=c.id, external_message_id=None))
    await db_session.commit()  # must not raise


# ── FK behaviour ────────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_contact_cascade_delete_removes_messages(db_session: Any) -> None:
    """Hard-DELETE on contact CASCADEs into outreach_message.

    Archive never fires this path — archive sets ``archived_at`` and
    leaves the row in place. CASCADE is the future guard for if
    hard-delete is ever exposed.
    """
    await _clear(db_session)
    c = _contact()
    db_session.add(c)
    await db_session.flush()
    db_session.add(_msg(contact_id=c.id))
    await db_session.commit()

    await db_session.execute(delete(Contact).where(Contact.id == c.id))
    await db_session.commit()

    remaining = (
        await db_session.execute(
            text("SELECT count(*) FROM outreach_message WHERE contact_id = :cid"),
            {"cid": str(c.id)},
        )
    ).scalar_one()
    assert remaining == 0
