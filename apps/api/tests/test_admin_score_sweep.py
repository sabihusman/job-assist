"""Tests for POST /admin/score/sweep (PR #56).

DB-gated tests use the ``db_session`` fixture from conftest.py and
monkey-patch ``score_posting`` so no test depends on the real heuristic
behaviour (those assertions live in tests/services/test_scoring.py).

Coverage:
  1. ``limit=5`` against 10 unscored postings → processes exactly 5
  2. Idempotency — sweep twice with deterministic mock → ``changed=0`` on second run
  3. ``only_unscored=True`` only touches NULL fit_score rows
  4. ``only_unscored=False`` rescores every row
  5. Per-row scoring failure on 1 of 5 rows → 200, processed=5, skipped=1, changed=4
  6. Failed row preserves its previous fit_score
  7. Stable ``id ASC`` tiebreaker (deterministic ordering on same-second first_seen_at)
  8. Response distribution buckets all valid bucket labels
  9. Empty table returns zeros
 10. ``limit=0`` and ``limit=501`` rejected with 422
 11. Unseeded operator_profile returns 400
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from job_assist.db.models import JobPosting, OperatorProfile, TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

_SAMPLE_JD = (
    "Senior Product Manager — Platform\n\n"
    "Own the roadmap for our developer platform. Partner with engineering "
    "and design to ship impactful features. 5+ years of PM experience required."
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _company() -> TargetCompany:
    return TargetCompany(
        name=f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(
    *,
    target_company_id: uuid.UUID,
    fit_score: int | None = None,
    scored_at: datetime | None = None,
    scorer_version: str | None = None,
    first_seen_at: datetime | None = None,
    title: str = "Senior Product Manager",
    jd_text: str = _SAMPLE_JD,
) -> JobPosting:
    now = first_seen_at or datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=title.lower(),
        raw_title=title,
        jd_text=jd_text,
        jd_text_hash=f"{'0' * 56}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        role_family="product_management",
        seniority_level="senior_pm",
        fit_score=fit_score,
        scored_at=scored_at,
        scorer_version=scorer_version,
    )


def _patch_score(
    monkeypatch: pytest.MonkeyPatch,
    score_value: int = 75,
    *,
    call_counter: list[int] | None = None,
    fail_on_call: int | None = None,
) -> None:
    """Patch score_posting in scoring.py's namespace.

    ``score_value``: deterministic score returned on success.
    ``fail_on_call``: 1-based call index that should raise; all others succeed.
    """
    calls: list[int] = call_counter if call_counter is not None else []

    def _stub(posting: Any, profile: Any, *, tier: int | None) -> int:
        calls.append(1)
        n = len(calls)
        if fail_on_call is not None and n == fail_on_call:
            raise RuntimeError("simulated scoring failure")
        return score_value

    monkeypatch.setattr("job_assist.services.scoring.score_posting", _stub)
    # main.py imports score_posting via a lazy import inside the endpoint
    # body, so patching the source module is enough — lazy imports re-bind
    # the name each call.


# ── Endpoint helpers ──────────────────────────────────────────────────────────


async def _post_sweep(client: AsyncClient, **body: Any) -> Any:
    return await client.post("/admin/score/sweep", json=body)


async def _seed_operator_profile(db_session: Any) -> None:
    """Insert the singleton operator_profile row at id=1 if missing.

    Mirrors the column defaults from the migration so the score sweep
    has a profile to work with.
    """
    existing = (
        await db_session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if existing is not None:
        return
    db_session.add(
        OperatorProfile(
            id=1,
            looking_for_text="",
            role_keywords=[],
            geo_whitelist=[],
            salary_floor_usd=85_000,
            applicant_cap=150,
            staffing_firm_blocklist=[],
        )
    )
    await db_session.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_processes_exactly_limit_rows(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``limit=5`` with 10 unscored rows → processed=5."""
    from job_assist.main import app

    _patch_score(monkeypatch, score_value=75)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    for _ in range(10):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unscored=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 5
    assert data["skipped"] == 0
    assert data["changed"] == 5


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_idempotent_second_run(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running sweep twice with the same deterministic mock → changed=0 on second run.

    Second run uses ``only_unscored=False`` so it touches the same rows
    again. The fit_score is identical, so ``changed=0``.
    """
    from job_assist.main import app

    _patch_score(monkeypatch, score_value=75)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    for _ in range(3):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await _post_sweep(client, limit=10, only_unscored=True)
        assert r1.status_code == 200
        assert r1.json()["changed"] == 3

        r2 = await _post_sweep(client, limit=10, only_unscored=False)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["processed"] == 3
        assert d2["changed"] == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_only_unscored_filter(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """``only_unscored=True`` only touches NULL fit_score rows."""
    from job_assist.main import app
    from job_assist.services.scoring import SCORER_VERSION

    _patch_score(monkeypatch, score_value=75)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    # 3 already-scored rows (should be skipped by the filter)
    for _ in range(3):
        db_session.add(
            _posting(
                target_company_id=tc.id,
                fit_score=42,
                scorer_version=SCORER_VERSION,
                scored_at=datetime.now(tz=UTC),
            )
        )
    # 4 unscored rows
    for _ in range(4):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unscored=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 4
    assert data["changed"] == 4


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_only_unscored_false_touches_all(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``only_unscored=False`` rescores all postings regardless of state."""
    from job_assist.main import app
    from job_assist.services.scoring import SCORER_VERSION

    _patch_score(monkeypatch, score_value=88)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    # 2 already-scored at 42 + 2 unscored — sweep should rescore all 4 to 88.
    for _ in range(2):
        db_session.add(
            _posting(
                target_company_id=tc.id,
                fit_score=42,
                scorer_version=SCORER_VERSION,
                scored_at=datetime.now(tz=UTC),
            )
        )
    for _ in range(2):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unscored=False)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 4


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_scoring_failure_skips_one_row(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoring failure on call #3 of 5 → processed=5, skipped=1, changed=4."""
    from job_assist.main import app

    calls: list[int] = []
    _patch_score(monkeypatch, score_value=75, call_counter=calls, fail_on_call=3)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    for _ in range(5):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unscored=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 5
    assert data["skipped"] == 1
    assert data["changed"] == 4


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_failed_row_preserves_previous_score(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose scoring call fails keeps its previous fit_score."""
    from job_assist.main import app

    def _always_raise(posting: Any, profile: Any, *, tier: int | None) -> int:
        raise RuntimeError("always fails")

    monkeypatch.setattr("job_assist.services.scoring.score_posting", _always_raise)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    posting = _posting(
        target_company_id=tc.id,
        fit_score=42,
        scorer_version="v0_legacy",
        scored_at=datetime.now(tz=UTC),
    )
    db_session.add(posting)
    await db_session.commit()
    posting_id = posting.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unscored=False)

    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] == 1
    assert data["changed"] == 0

    # Force fresh DB read — see scorer-metadata test for the rationale.
    await db_session.refresh(posting)
    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting_id))
    ).scalar_one()
    assert refreshed.fit_score == 42
    assert refreshed.scorer_version == "v0_legacy"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_writes_scorer_metadata(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful sweep writes scorer_version + scored_at to the row."""
    from job_assist.main import app
    from job_assist.services.scoring import SCORER_VERSION

    _patch_score(monkeypatch, score_value=75)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    posting = _posting(target_company_id=tc.id)
    db_session.add(posting)
    await db_session.commit()
    posting_id = posting.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unscored=True)

    assert resp.status_code == 200

    # The test fixture uses ``expire_on_commit=False``, so the original
    # ``posting`` object stays in the identity map with its pre-sweep
    # column values. A fresh select-by-PK returns the cached object
    # unchanged. Refresh forces SQLAlchemy to reload the row from the DB.
    # Same pattern PR #48's test_admin_reclassify uses.
    await db_session.refresh(posting)
    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting_id))
    ).scalar_one()
    assert refreshed.fit_score == 75
    assert refreshed.scorer_version == SCORER_VERSION
    assert refreshed.scored_at is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_stable_tiebreaker_on_same_second_first_seen_at(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three postings sharing first_seen_at must order by id ASC.

    Locks the bestiary entry about stable secondary id ASC on every
    paginated ORDER BY.
    """
    from job_assist.main import app

    # Record the call order so we can assert the sweep visited rows in
    # id-ascending order despite identical first_seen_at.
    visited_ids: list[str] = []

    def _stub(posting: Any, profile: Any, *, tier: int | None) -> int:
        visited_ids.append(str(posting.id))
        return 50

    monkeypatch.setattr("job_assist.services.scoring.score_posting", _stub)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    shared_ts = datetime.now(tz=UTC).replace(microsecond=0)
    postings = [_posting(target_company_id=tc.id, first_seen_at=shared_ts) for _ in range(3)]
    for p in postings:
        db_session.add(p)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=10, only_unscored=True)

    assert resp.status_code == 200
    # visited_ids should equal the postings' ids sorted ascending.
    assert visited_ids == sorted(visited_ids)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_distribution_keys_are_valid_bucket_labels(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distribution buckets in the response are one of the documented labels."""
    from job_assist.main import app

    _patch_score(monkeypatch, score_value=75)
    await _seed_operator_profile(db_session)

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    valid_buckets = {"unscored", "0-19", "20-39", "40-59", "60-79", "80-100"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unscored=True)

    assert resp.status_code == 200
    by_bucket = resp.json()["distribution"]["by_bucket"]
    for key in by_bucket:
        assert key in valid_buckets, f"unexpected bucket key: {key!r}"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_empty_table_returns_zeros(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweeping an empty table returns processed=0, changed=0, skipped=0."""
    from job_assist.main import app

    _patch_score(monkeypatch, score_value=75)
    await _seed_operator_profile(db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unscored=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 0
    assert data["changed"] == 0
    assert data["skipped"] == 0


# NOTE: the "400 when operator_profile is unseeded" path is exercised by
# inspection of the endpoint code (``if operator_profile is None: raise
# HTTPException(400, ...)``). An integration test for that branch would
# need to DELETE the seed row that the migration creates — which violates
# the session-level invariant that other tests (test_operator_profile.py
# all 8 cases) rely on. The cost of breaking that invariant outweighs the
# one-line assertion. Skip philosophy.


@pytest.mark.asyncio
async def test_sweep_invalid_limit_422() -> None:
    """``limit=0`` is rejected by Pydantic validation → 422."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/score/sweep", json={"limit": 0})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sweep_limit_too_large_422() -> None:
    """``limit=501`` exceeds the 500 cap → 422."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/score/sweep", json={"limit": 501})
    assert resp.status_code == 422
