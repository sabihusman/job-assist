"""End-to-end tests for ``POST /admin/seed/contacts`` (PR #39).

DB-gated. Synthetic data only — never real PII.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from job_assist.db.models.contact import Contact

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _row(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "first_name": "Pat",
        "last_name": "Smith",
        "email_primary": "pat@example.com",
        "source_type": "tippie_alumni",
    }
    base.update(overrides)
    return base


async def _post(db_session: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/admin/seed/contacts", json=rows)
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── 9 ─────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_inserts_new_rows(db_session: Any) -> None:
    body = await _post(
        db_session,
        [
            _row(email_primary="a@example.com"),
            _row(first_name="Q", last_name="R", email_primary="b@example.com"),
        ],
    )
    assert body["inserted"] == 2
    assert body["total"] == 2

    count = (await db_session.execute(select(Contact))).scalars().all()
    assert len(count) == 2


# ── 10 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_idempotent_skips_duplicate_email(db_session: Any) -> None:
    await _post(db_session, [_row(email_primary="dup@example.com")])
    body = await _post(db_session, [_row(email_primary="dup@example.com")])
    assert body["inserted"] == 0
    assert body["skipped_duplicate_email"] == 1
    rows = (await db_session.execute(select(Contact))).scalars().all()
    assert len(rows) == 1


# ── 11 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_idempotent_skips_duplicate_linkedin(db_session: Any) -> None:
    await _post(
        db_session,
        [_row(email_primary=None, linkedin_url="https://linkedin.com/in/dup")],
    )
    body = await _post(
        db_session,
        [_row(email_primary=None, linkedin_url="https://linkedin.com/in/dup")],
    )
    assert body["inserted"] == 0
    assert body["skipped_duplicate_linkedin"] == 1


# ── 12 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_email_dedup_is_case_insensitive(db_session: Any) -> None:
    await _post(db_session, [_row(email_primary="MixedCase@Example.com")])
    body = await _post(db_session, [_row(email_primary="mixedcase@example.com")])
    assert body["skipped_duplicate_email"] == 1
    assert body["inserted"] == 0


# ── 13 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_linkedin_dedup_is_case_insensitive(db_session: Any) -> None:
    # Both inputs normalize to https://linkedin.com/in/foo; the partial
    # LOWER() index then catches even hand-crafted dup variants.
    await _post(
        db_session,
        [_row(email_primary=None, linkedin_url="https://www.linkedin.com/in/FOO/")],
    )
    body = await _post(
        db_session,
        [_row(email_primary=None, linkedin_url="linkedin.com/in/foo")],
    )
    assert body["skipped_duplicate_linkedin"] == 1


# ── 14 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_response_counts_correct(db_session: Any) -> None:
    # 2 valid, 1 dup-email, 1 invalid (no channel), 1 invalid (no name).
    await _post(db_session, [_row(email_primary="seeded@example.com")])
    body = await _post(
        db_session,
        [
            _row(email_primary="new1@example.com"),
            _row(email_primary="new2@example.com"),
            _row(email_primary="seeded@example.com"),  # dup
            _row(email_primary=None, linkedin_url=None),  # invalid
            _row(first_name="", email_primary="other@example.com"),  # invalid
        ],
    )
    assert body["inserted"] == 2
    assert body["skipped_duplicate_email"] == 1
    assert body["skipped_invalid"] == 2
    assert body["total"] == 5


# ── 15 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_rejects_row_with_no_name(db_session: Any) -> None:
    body = await _post(
        db_session,
        [
            _row(first_name="", last_name="X", email_primary="a@example.com"),
            _row(email_primary="b@example.com"),  # valid
        ],
    )
    assert body["inserted"] == 1
    assert body["skipped_invalid"] == 1


# ── 16 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_rejects_row_with_no_contact_channel(db_session: Any) -> None:
    body = await _post(
        db_session,
        [_row(email_primary=None, linkedin_url=None)],
    )
    assert body["inserted"] == 0
    assert body["skipped_invalid"] == 1


# ── 17 ────────────────────────────────────────────────────────────────────────
@_NEEDS_DB
async def test_seed_no_pii_in_response_or_logs(db_session: Any) -> None:
    """Response shape must contain only count fields — no names, emails, urls."""
    body = await _post(
        db_session,
        [_row(email_primary="secret-pii@example.com", first_name="Confidential")],
    )
    expected_keys = {
        "inserted",
        "skipped_duplicate_email",
        "skipped_duplicate_linkedin",
        "skipped_invalid",
        "total",
    }
    assert set(body.keys()) == expected_keys
    flat = str(body)
    assert "secret-pii" not in flat
    assert "Confidential" not in flat
