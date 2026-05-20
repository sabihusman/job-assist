"""DB-gated assertions about the contact migration (PR #39).

These confirm the migration produced the columns, partial LOWER()
unique indexes, and CHECK constraints described in the spec — not the
ORM model. (The ORM tests cover ``Contact`` directly.)
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── 18 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_migration_creates_table_with_correct_columns(db_session: Any) -> None:
    cols = (
        (
            await db_session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'contact'"
                )
            )
        )
        .scalars()
        .all()
    )
    expected = {
        "id",
        "first_name",
        "last_name",
        "preferred_first_name",
        "email_primary",
        "email_secondary",
        "linkedin_url",
        "current_employer",
        "current_position",
        "location_city",
        "location_state",
        "location_country",
        "location_metro",
        "source_type",
        "source_metadata",
        "job_functions_of_interest",
        "industries_of_interest",
        "contact_opt_in",
        "contact_opt_in_topics",
        "notes",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(set(cols))


# ── 19 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_migration_email_unique_index_is_partial_lower(db_session: Any) -> None:
    """uq_contact_email_primary must be UNIQUE, partial (WHERE email_primary
    IS NOT NULL), and on LOWER(email_primary)."""
    row = (
        await db_session.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_contact_email_primary'")
        )
    ).scalar_one_or_none()
    assert row is not None, "index missing"
    defn = row.lower()
    assert "unique" in defn
    assert "lower" in defn
    assert "email_primary is not null" in defn


# ── 20 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_migration_linkedin_unique_index_is_partial_lower(db_session: Any) -> None:
    row = (
        await db_session.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_contact_linkedin_url'")
        )
    ).scalar_one_or_none()
    assert row is not None, "index missing"
    defn = row.lower()
    assert "unique" in defn
    assert "lower" in defn
    assert "linkedin_url is not null" in defn


# ── 21 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_migration_check_constraint_at_least_one_channel(db_session: Any) -> None:
    """Inserting a row with no email and no linkedin must fail."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO contact (first_name, last_name, source_type) "
                "VALUES ('A', 'B', 'tippie_alumni')"
            )
        )
    await db_session.rollback()


# ── 22 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_migration_check_constraint_source_type_enum(db_session: Any) -> None:
    """source_type outside the four allowed values must fail."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO contact "
                "(first_name, last_name, source_type, email_primary) "
                "VALUES ('A', 'B', 'bogus_source', 'x@y.co')"
            )
        )
    await db_session.rollback()
