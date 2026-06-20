"""Tests for the singleton operator_profile model + GET/PUT endpoints.

The conftest's per-test TRUNCATE intentionally excludes ``operator_profile``
so the migration-seeded id=1 row survives across tests. Each test that
needs a known starting state resets the singleton via the
``reset_operator_profile`` fixture below.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from job_assist.db.models import OperatorProfile

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# Mirrors triage.config.HardRuleConfig + the migration's seed payload.
_SEED_GEO_WHITELIST = [
    "Remote",
    "Des Moines",
    "NYC",
    "New York",
    "Austin",
    "San Francisco",
    "Bay Area",
    "Seattle",
    "Minneapolis",
    "Chicago",
]
_SEED_STAFFING_FIRM_BLOCKLIST = [
    "Robert Half",
    "Aerotek",
    "Insight Global",
    "Apex Systems",
    "Beacon Hill",
    "TEKsystems",
    "Modis",
    "Randstad",
    "Kforce",
    "Adecco",
]


# ── Helpers ────────────────────────────────────────────────────────────────────


@pytest.fixture
async def reset_operator_profile(db_session: Any) -> Any:
    """Reset the singleton to the migration-seeded defaults before each test."""
    import json

    await db_session.execute(
        sa.text(
            """
            UPDATE operator_profile
               SET looking_for_text = '',
                   role_keywords = '[]'::jsonb,
                   geo_whitelist = CAST(:geo AS jsonb),
                   salary_floor_usd = 85000,
                   applicant_cap = 500,
                   staffing_firm_blocklist = CAST(:blocklist AS jsonb)
             WHERE id = 1
            """
        ),
        {
            "geo": json.dumps(_SEED_GEO_WHITELIST),
            "blocklist": json.dumps(_SEED_STAFFING_FIRM_BLOCKLIST),
        },
    )
    await db_session.commit()
    yield


async def _client(db_session: Any) -> AsyncClient:
    """ASGI client with the test session injected as the get_db dependency."""
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


# ── Tests ──────────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_migration_seeds_row(db_session: Any, reset_operator_profile: Any) -> None:
    """Fresh migration → GET returns the seeded singleton with HardRuleConfig defaults."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/operator/profile")
    finally:
        await _drop_override()

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["looking_for_text"] == ""
    assert body["role_keywords"] == []
    assert body["geo_whitelist"] == _SEED_GEO_WHITELIST
    assert body["salary_floor_usd"] == 85_000
    assert body["applicant_cap"] == 500
    assert body["staffing_firm_blocklist"] == _SEED_STAFFING_FIRM_BLOCKLIST
    assert "created_at" in body and "updated_at" in body


@_NEEDS_DB
async def test_get_returns_singleton(db_session: Any, reset_operator_profile: Any) -> None:
    """Two GETs in a row return the same row, with id=1."""
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.get("/operator/profile")
            r2 = await ac.get("/operator/profile")
    finally:
        await _drop_override()

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == 1
    assert r2.json()["id"] == 1


@_NEEDS_DB
async def test_put_partial_update(db_session: Any, reset_operator_profile: Any) -> None:
    """PUT touches only the fields present in the body; everything else unchanged."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put(
                "/operator/profile",
                json={"looking_for_text": "Senior PM in fintech, remote-US"},
            )
    finally:
        await _drop_override()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["looking_for_text"] == "Senior PM in fintech, remote-US"
    # Untouched fields keep the seeded defaults.
    assert body["geo_whitelist"] == _SEED_GEO_WHITELIST
    assert body["salary_floor_usd"] == 85_000
    assert body["applicant_cap"] == 500
    assert body["staffing_firm_blocklist"] == _SEED_STAFFING_FIRM_BLOCKLIST


@_NEEDS_DB
async def test_put_updates_numeric_threshold(db_session: Any, reset_operator_profile: Any) -> None:
    """Numeric fields update independently of the list fields."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put(
                "/operator/profile",
                json={"salary_floor_usd": 120_000, "applicant_cap": 200},
            )
    finally:
        await _drop_override()

    assert resp.status_code == 200
    body = resp.json()
    assert body["salary_floor_usd"] == 120_000
    assert body["applicant_cap"] == 200
    assert body["geo_whitelist"] == _SEED_GEO_WHITELIST  # untouched


@_NEEDS_DB
async def test_put_validates_role_keywords_rejects_empty_string(
    db_session: Any, reset_operator_profile: Any
) -> None:
    """An empty string inside ``role_keywords`` fails validation with 422."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put(
                "/operator/profile",
                json={"role_keywords": ["product manager", "", "fintech"]},
            )
    finally:
        await _drop_override()

    assert resp.status_code == 422
    assert "empty strings" in resp.text.lower()


@_NEEDS_DB
async def test_put_dedupes_geo_whitelist(db_session: Any, reset_operator_profile: Any) -> None:
    """Duplicate (after whitespace trim) entries are collapsed; order preserved."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put(
                "/operator/profile",
                json={"geo_whitelist": ["Remote", "  Remote  ", "Austin", "Austin"]},
            )
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert resp.json()["geo_whitelist"] == ["Remote", "Austin"]


@_NEEDS_DB
async def test_put_strips_whitespace_in_lists(db_session: Any, reset_operator_profile: Any) -> None:
    """List items have leading/trailing whitespace stripped."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put(
                "/operator/profile",
                json={"role_keywords": ["  product manager  ", "fintech "]},
            )
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert resp.json()["role_keywords"] == ["product manager", "fintech"]


@_NEEDS_DB
async def test_put_rejects_negative_threshold(db_session: Any, reset_operator_profile: Any) -> None:
    """Negative salary_floor_usd / applicant_cap rejected with 422."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp_a = await ac.put("/operator/profile", json={"salary_floor_usd": -1})
            resp_b = await ac.put("/operator/profile", json={"applicant_cap": -50})
    finally:
        await _drop_override()

    assert resp_a.status_code == 422
    assert resp_b.status_code == 422


@_NEEDS_DB
async def test_put_rejects_unknown_field(db_session: Any, reset_operator_profile: Any) -> None:
    """``extra='forbid'`` on the Update schema rejects unrecognised keys."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put("/operator/profile", json={"not_a_real_field": "oops"})
    finally:
        await _drop_override()

    assert resp.status_code == 422


@_NEEDS_DB
async def test_put_persists_across_sessions(db_session: Any, reset_operator_profile: Any) -> None:
    """A PUT followed by a fresh GET on the same row returns the updated values."""
    ac = await _client(db_session)
    try:
        async with ac:
            await ac.put(
                "/operator/profile",
                json={"looking_for_text": "persisted across the request"},
            )
            resp_get = await ac.get("/operator/profile")
    finally:
        await _drop_override()

    assert resp_get.json()["looking_for_text"] == "persisted across the request"


@_NEEDS_DB
async def test_singleton_constraint_rejects_id_2(
    db_session: Any, reset_operator_profile: Any
) -> None:
    """Direct INSERT with id=2 violates the CHECK constraint."""
    with pytest.raises(IntegrityError):
        await db_session.execute(sa.text("INSERT INTO operator_profile (id) VALUES (2)"))
        await db_session.commit()
    await db_session.rollback()

    # id=1 still exists and is unchanged.
    row = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one()
    assert row.id == 1


# ── A3: applied_corpus_weight tunable + rescore-on-change ────────────────────


def _patch_save_hook(monkeypatch: Any) -> dict[str, int]:
    """Stub the profile-save side effects so the rescore trigger is observable
    without a Gemini call or a real corpus rescore.

    The PUT handler imports these lazily inside the function, so patching the
    module attributes is picked up at call time. ``embed_profile_if_changed``
    is forced to report "unchanged" (looking_for_text untouched in these tests)
    so only the applied_corpus_weight branch can fire the rescore.
    """
    import job_assist.services.embeddings as embeddings_mod
    import job_assist.services.rescore as rescore_mod

    counts = {"rescore": 0}

    async def _no_embed_change(_db: Any) -> bool:
        return False

    async def _count_rescore(_db: Any, **_kw: Any) -> tuple[int, int]:
        counts["rescore"] += 1
        return (0, 0)

    monkeypatch.setattr(embeddings_mod, "embed_profile_if_changed", _no_embed_change)
    monkeypatch.setattr(rescore_mod, "rescore_open_postings", _count_rescore)
    return counts


@_NEEDS_DB
async def test_put_applied_corpus_weight_change_triggers_rescore(
    db_session: Any, reset_operator_profile: Any, monkeypatch: Any
) -> None:
    """A real applied_corpus_weight change (0 → 0.1) fires a single rescore and
    round-trips the new value."""
    counts = _patch_save_hook(monkeypatch)
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.put("/operator/profile", json={"applied_corpus_weight": 0.1})
    finally:
        await _drop_override()

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied_corpus_weight"] == 0.1
    assert counts["rescore"] == 1


@_NEEDS_DB
async def test_put_applied_corpus_weight_unchanged_no_rescore(
    db_session: Any, reset_operator_profile: Any, monkeypatch: Any
) -> None:
    """Setting the weight to its current value (0 → 0), or changing an unrelated
    field, does NOT trigger a rescore."""
    counts = _patch_save_hook(monkeypatch)
    ac = await _client(db_session)
    try:
        async with ac:
            # Same value as the seeded default (0) — no change, no rescore.
            r_same = await ac.put("/operator/profile", json={"applied_corpus_weight": 0.0})
            # Field absent entirely — no rescore.
            r_other = await ac.put("/operator/profile", json={"salary_floor_usd": 90_000})
    finally:
        await _drop_override()

    assert r_same.status_code == 200 and r_other.status_code == 200
    assert counts["rescore"] == 0


@_NEEDS_DB
async def test_put_applied_corpus_weight_to_zero_triggers_rescore(
    db_session: Any, reset_operator_profile: Any, monkeypatch: Any
) -> None:
    """Turning the boost OFF (0.1 → 0) must also rescore so scores revert cleanly
    to pure fit_score — not leave stale boosted values behind."""
    counts = _patch_save_hook(monkeypatch)
    ac = await _client(db_session)
    try:
        async with ac:
            await ac.put("/operator/profile", json={"applied_corpus_weight": 0.1})
            assert counts["rescore"] == 1
            resp = await ac.put("/operator/profile", json={"applied_corpus_weight": 0.0})
    finally:
        await _drop_override()

    assert resp.status_code == 200
    assert resp.json()["applied_corpus_weight"] == 0.0
    assert counts["rescore"] == 2  # the 0.1→0 change fired a second rescore


@_NEEDS_DB
async def test_put_rejects_out_of_range_applied_corpus_weight(
    db_session: Any, reset_operator_profile: Any
) -> None:
    """The validator rejects weights outside [0, 1] with 422."""
    ac = await _client(db_session)
    try:
        async with ac:
            resp_hi = await ac.put("/operator/profile", json={"applied_corpus_weight": 1.5})
            resp_lo = await ac.put("/operator/profile", json={"applied_corpus_weight": -0.1})
    finally:
        await _drop_override()

    assert resp_hi.status_code == 422
    assert resp_lo.status_code == 422
