"""Tests for the async reclassify sweep (PR #48 → D-ASYNC-RESWEEP).

DB-gated tests use the ``db_session`` fixture from conftest.py and
monkey-patch ``classify_posting`` so no test ever calls the real Gemini API.

The per-batch sweep behavior (limit, only_unclassified filter, same-version
skip, LLM-failure handling, metadata writes) is tested against
``services/reclassify_sweep.process_reclassify_batch`` — the moved-verbatim
sweep body. The job lifecycle (queued → running → succeeded/failed, batch
counter updates, DB-derived resumption) is tested against
``run_reclassify_job``. The two endpoints are tested for their HTTP
contract: POST → 202 {job_id, status} + a persisted job row; GET → the row.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from job_assist.db.models import JobPosting, ReclassifyJob, TargetCompany
from job_assist.services.classifier import CLASSIFIER_VERSION

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
    role_family: str = "other",
    seniority_level: str = "unknown",
    classified_at: datetime | None = None,
    classifier_version: str | None = None,
    title: str = "Senior Product Manager",
    jd_text: str = _SAMPLE_JD,
) -> JobPosting:
    now = datetime.now(tz=UTC)
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
        role_family=role_family,  # type: ignore[arg-type]
        seniority_level=seniority_level,  # type: ignore[arg-type]
        classified_at=classified_at,
        classifier_version=classifier_version,
    )


def _patch_classify(
    monkeypatch: pytest.MonkeyPatch,
    result_or_exc: Any,
    *,
    call_counter: list[int] | None = None,
    fail_on_call: int | None = None,
) -> None:
    """Patch classify_posting in the classifier module.

    ``services/reclassify_sweep.process_reclassify_batch`` imports it lazily
    via ``from job_assist.services.classifier import classify_posting``, so
    patching the source module is sufficient.
    """
    calls: list[int] = call_counter if call_counter is not None else []

    async def _stub(jd_text: str, title: str, **_: Any) -> tuple[str, str]:
        calls.append(1)
        n = len(calls)
        if fail_on_call is not None and n == fail_on_call:
            raise RuntimeError("simulated Gemini failure")
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc  # type: ignore[return-value]

    monkeypatch.setattr("job_assist.services.classifier.classify_posting", _stub)


async def _seed_pool(db_session: Any, count: int, **posting_kwargs: Any) -> TargetCompany:
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    for _ in range(count):
        db_session.add(_posting(target_company_id=tc.id, **posting_kwargs))
    await db_session.commit()
    return tc


async def _make_job(db_session: Any, *, requested_limit: int) -> ReclassifyJob:
    job = ReclassifyJob(status="queued", requested_limit=requested_limit)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


async def _get_job(db_session: Any, job_id: uuid.UUID) -> ReclassifyJob:
    # The worker commits through its own autonomous sessions; this fixture
    # session's identity map still holds the pre-run instance (the fixture
    # uses expire_on_commit=False), and a re-SELECT by PK returns the CACHED
    # object with stale attributes — same gotcha the old sync tests hit
    # (see PR #48's refresh note). Expire everything so the SELECT reloads.
    db_session.expire_all()
    return (
        await db_session.execute(select(ReclassifyJob).where(ReclassifyJob.id == job_id))
    ).scalar_one()


# ── process_reclassify_batch — moved-verbatim sweep behavior ─────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_batch_processes_exactly_batch_size_rows(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch_size=5 with 10 eligible rows → processed=5."""
    from job_assist.services.reclassify_sweep import process_reclassify_batch

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    await _seed_pool(db_session, 10)

    processed, changed, skipped = await process_reclassify_batch(
        db_session, batch_size=5, only_unclassified=True
    )
    assert (processed, changed, skipped) == (5, 5, 0)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_batch_only_unclassified_filter(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """only_unclassified=True only touches 'other'/'unknown' rows."""
    from job_assist.services.reclassify_sweep import process_reclassify_batch

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    tc = await _seed_pool(db_session, 4)  # 4 unclassified
    for _ in range(3):  # 3 already-classified — skipped by the filter
        db_session.add(
            _posting(
                target_company_id=tc.id,
                role_family="program_management",
                seniority_level="lead_pm",
                classifier_version=CLASSIFIER_VERSION,
                classified_at=datetime.now(tz=UTC),
            )
        )
    await db_session.commit()

    processed, changed, _ = await process_reclassify_batch(
        db_session, batch_size=50, only_unclassified=True
    )
    assert (processed, changed) == (4, 4)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_batch_does_not_rebuy_llm_confirmed_other(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fix(audit) carried over: a row THIS version already judged 'other'
    must not be re-selected; an older-version row stays re-keyable."""
    from job_assist.services.reclassify_sweep import process_reclassify_batch

    _patch_classify(monkeypatch, ("other", "unknown"))
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    db_session.add(
        _posting(
            target_company_id=tc.id,
            classifier_version=CLASSIFIER_VERSION,
            classified_at=datetime.now(tz=UTC),
        )
    )
    db_session.add(
        _posting(
            target_company_id=tc.id,
            classifier_version="gemini-flash-lite-v0-legacy",
            classified_at=datetime.now(tz=UTC),
        )
    )
    db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    processed, _, _ = await process_reclassify_batch(
        db_session, batch_size=50, only_unclassified=True
    )
    assert processed == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_batch_llm_failure_skips_row_and_preserves_values(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM failure on 1 of 5 → processed=5, skipped=1, changed=4; the failed
    row keeps its original values."""
    from job_assist.services.reclassify_sweep import process_reclassify_batch

    calls: list[int] = []
    _patch_classify(
        monkeypatch, ("product_management", "senior_pm"), call_counter=calls, fail_on_call=3
    )
    await _seed_pool(db_session, 5)

    processed, changed, skipped = await process_reclassify_batch(
        db_session, batch_size=5, only_unclassified=True
    )
    assert (processed, changed, skipped) == (5, 4, 1)

    untouched = (
        (await db_session.execute(select(JobPosting).where(JobPosting.classified_at.is_(None))))
        .scalars()
        .all()
    )
    assert len(untouched) == 1
    assert str(untouched[0].role_family) == "other"
    assert str(untouched[0].seniority_level) == "unknown"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_batch_writes_classifier_metadata(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.reclassify_sweep import process_reclassify_batch

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    await _seed_pool(db_session, 1)

    await process_reclassify_batch(db_session, batch_size=5, only_unclassified=True)

    row = (await db_session.execute(select(JobPosting))).scalar_one()
    assert row.classifier_version == CLASSIFIER_VERSION
    assert row.classified_at is not None


# ── run_reclassify_job — lifecycle, batching, resumption ─────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_job_lifecycle_succeeds_with_counters(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job over a 7-row pool with limit 10 → succeeded, processed=7,
    changed=7, finished_at set (pool exhausted before budget)."""
    from job_assist.services.reclassify_sweep import run_reclassify_job

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    await _seed_pool(db_session, 7)
    job = await _make_job(db_session, requested_limit=10)

    await run_reclassify_job(job.id, only_unclassified=True)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "succeeded"
    assert refreshed.processed == 7
    assert refreshed.changed == 7
    assert refreshed.error is None
    assert refreshed.finished_at is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_job_runs_multiple_internal_batches(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A limit above the internal batch size (50) forces >1 batch; the job
    row ends with the full total (counters accumulated across batches)."""
    from job_assist.services.reclassify_sweep import run_reclassify_job

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    await _seed_pool(db_session, 60)
    job = await _make_job(db_session, requested_limit=60)

    await run_reclassify_job(job.id, only_unclassified=True)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "succeeded"
    assert refreshed.processed == 60  # 50 + 10 across two internal batches


@_NEEDS_DB
@pytest.mark.asyncio
async def test_job_respects_requested_limit_budget(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.reclassify_sweep import run_reclassify_job

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    await _seed_pool(db_session, 10)
    job = await _make_job(db_session, requested_limit=5)

    await run_reclassify_job(job.id, only_unclassified=True)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "succeeded"
    assert refreshed.processed == 5


@_NEEDS_DB
@pytest.mark.asyncio
async def test_refired_job_resumes_remaining_work_from_db(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RESUMPTION contract: job A (limit 5) judges 5 of 10; a NEW job B on
    the same pool derives the remaining 5 from the DB query — nothing is
    double-bought, nothing is lost."""
    from job_assist.services.reclassify_sweep import run_reclassify_job

    calls: list[int] = []
    _patch_classify(monkeypatch, ("product_management", "senior_pm"), call_counter=calls)
    await _seed_pool(db_session, 10)

    job_a = await _make_job(db_session, requested_limit=5)
    await run_reclassify_job(job_a.id, only_unclassified=True)
    assert (await _get_job(db_session, job_a.id)).processed == 5

    job_b = await _make_job(db_session, requested_limit=50)
    await run_reclassify_job(job_b.id, only_unclassified=True)

    refreshed_b = await _get_job(db_session, job_b.id)
    assert refreshed_b.status == "succeeded"
    assert refreshed_b.processed == 5  # only the 5 still-unjudged rows
    assert len(calls) == 10  # no row classified twice


@_NEEDS_DB
@pytest.mark.asyncio
async def test_job_failure_lands_on_row(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch-level exception marks the job failed with the error message."""
    import job_assist.services.reclassify_sweep as sweep_mod

    async def _boom(*_: Any, **__: Any) -> tuple[int, int, int]:
        raise RuntimeError("simulated batch crash")

    monkeypatch.setattr(sweep_mod, "process_reclassify_batch", _boom)
    job = await _make_job(db_session, requested_limit=5)

    await sweep_mod.run_reclassify_job(job.id, only_unclassified=True)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "failed"
    assert "simulated batch crash" in (refreshed.error or "")
    assert refreshed.finished_at is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_job_empty_pool_succeeds_with_zeros(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.reclassify_sweep import run_reclassify_job

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))
    job = await _make_job(db_session, requested_limit=50)

    await run_reclassify_job(job.id, only_unclassified=True)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "succeeded"
    assert refreshed.processed == 0
    assert refreshed.changed == 0


# ── Endpoints — HTTP contract ─────────────────────────────────────────────────


def _patch_spawn(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, bool]]:
    """No-op the fire-and-forget spawn so endpoint tests stay deterministic
    (the worker's own behavior is covered by the run_reclassify_job tests)."""
    spawned: list[tuple[uuid.UUID, bool]] = []

    def _record(job_id: uuid.UUID, *, only_unclassified: bool) -> None:
        spawned.append((job_id, only_unclassified))

    monkeypatch.setattr(
        "job_assist.services.reclassify_sweep.spawn_reclassify_job",
        _record,
    )
    return spawned


@_NEEDS_DB
@pytest.mark.asyncio
async def test_post_sweep_returns_202_with_job_row(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.main import app

    spawned = _patch_spawn(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/admin/reclassify/sweep",
            json={"limit": 25, "only_unclassified": False},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    job_id = uuid.UUID(data["job_id"])

    # The job row is persisted with the request's limit...
    job = await _get_job(db_session, job_id)
    assert job.status == "queued"
    assert job.requested_limit == 25
    assert job.processed == 0
    # ...and the worker was kicked off with the request's only_unclassified.
    assert spawned == [(job_id, False)]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_get_job_returns_row(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_assist.main import app

    job = await _make_job(db_session, requested_limit=100)
    job.status = "running"
    job.processed = 50
    job.changed = 12
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/admin/reclassify/jobs/{job.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == str(job.id)
    assert data["status"] == "running"
    assert data["requested_limit"] == 100
    assert data["processed"] == 50
    assert data["changed"] == 12
    assert data["error"] is None
    assert data["finished_at"] is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_get_job_unknown_id_404(db_session: Any) -> None:
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/admin/reclassify/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


@_NEEDS_DB
@pytest.mark.asyncio
async def test_post_sweep_invalid_limit_422(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """limit=0 and limit above the 5000 cap are rejected by validation."""
    from job_assist.main import app

    _patch_spawn(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/admin/reclassify/sweep", json={"limit": 0})).status_code == 422
        assert (
            await client.post("/admin/reclassify/sweep", json={"limit": 5001})
        ).status_code == 422
