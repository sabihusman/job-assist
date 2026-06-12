"""Tests for POST /admin/reclassify/sweep (PR #48).

DB-gated tests use the ``db_session`` fixture from conftest.py and
monkey-patch ``classify_posting`` so no test ever calls the real Gemini API.

Coverage:
  1. limit=5 against 10 postings → processes exactly 5
  2. Idempotency — sweep twice with deterministic mock → changed=0 on second run
  3. only_unclassified=true → only touches 'other'/'unknown' rows
  4. only_unclassified=false → touches all rows
  5. LLM failure on 1 of 5 rows → 200, processed=5, skipped=1, changed=4
  6. Response schema — distribution keys match valid enum values
  7. classifier_version + classified_at written on success
  8. Failed row preserves original values unchanged
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from job_assist.db.models import JobPosting, TargetCompany
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
    """Patch classify_posting in main.py's import namespace.

    ``result_or_exc``: a (family, seniority) tuple for success, or an
    Exception instance to raise (on all calls, or only on ``fail_on_call``).
    ``fail_on_call``: 1-based call index that should raise; all others succeed.
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

    # Patch both the service module and main.py's lazy-import reference.
    monkeypatch.setattr("job_assist.services.classifier.classify_posting", _stub)
    # main.py imports classify_posting inside the endpoint function via a
    # lazy ``from job_assist.services.classifier import classify_posting``,
    # so patching the source module is sufficient.


# ── Endpoint helpers ──────────────────────────────────────────────────────────


async def _post_sweep(client: AsyncClient, **body: Any) -> Any:
    resp = await client.post("/admin/reclassify/sweep", json=body)
    return resp


# ── Tests ─────────────────────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_processes_exactly_limit_rows(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """limit=5 with 10 eligible rows → processed=5."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    postings = [_posting(target_company_id=tc.id) for _ in range(10)]
    for p in postings:
        db_session.add(p)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unclassified=True)

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
    """Running sweep twice with same deterministic mock → changed=0 on second run."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    for _ in range(3):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await _post_sweep(client, limit=10, only_unclassified=True)
        assert r1.status_code == 200
        assert r1.json()["changed"] == 3

        # Second sweep: all rows now have role_family='product_management',
        # seniority_level='senior_pm' — no longer 'other'/'unknown'.
        # only_unclassified=True → nothing selected → processed=0, changed=0.
        r2 = await _post_sweep(client, limit=10, only_unclassified=True)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["processed"] == 0
        assert d2["changed"] == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_only_unclassified_filter(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """only_unclassified=True only touches 'other'/'unknown' rows."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    # 3 already-classified rows (should be skipped by the filter)
    for _ in range(3):
        db_session.add(
            _posting(
                target_company_id=tc.id,
                role_family="program_management",
                seniority_level="lead_pm",
                classifier_version=CLASSIFIER_VERSION,
                classified_at=datetime.now(tz=UTC),
            )
        )
    # 4 unclassified rows
    for _ in range(4):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unclassified=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 4
    assert data["changed"] == 4


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_only_unclassified_false_touches_all(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """only_unclassified=False reclassifies all rows regardless of current values."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    # 2 already-classified + 2 unclassified
    for _ in range(2):
        db_session.add(
            _posting(
                target_company_id=tc.id,
                role_family="program_management",
                seniority_level="lead_pm",
                classifier_version=CLASSIFIER_VERSION,
                classified_at=datetime.now(tz=UTC),
            )
        )
    for _ in range(2):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unclassified=False)

    assert resp.status_code == 200
    data = resp.json()
    # All 4 rows processed; the 2 already-classified change family/seniority
    assert data["processed"] == 4


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_does_not_rebuy_llm_confirmed_other(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fix(audit): a row THIS classifier version already judged 'other' must
    not be re-selected by only_unclassified — pre-fix it stayed in the bucket
    forever and was re-sent to Gemini daily, producing the same answer at the
    same CLASSIFIER_VERSION (pure wasted paid calls). A row classified by an
    OLDER version stays re-keyable."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("other", "unknown"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    # LLM-confirmed 'other' at the CURRENT version → must be skipped.
    db_session.add(
        _posting(
            target_company_id=tc.id,
            role_family="other",
            seniority_level="unknown",
            classifier_version=CLASSIFIER_VERSION,
            classified_at=datetime.now(tz=UTC),
        )
    )
    # Same bucket, but classified by an OLDER version → re-keyable, selected.
    db_session.add(
        _posting(
            target_company_id=tc.id,
            role_family="other",
            seniority_level="unknown",
            classifier_version="gemini-flash-lite-v0-legacy",
            classified_at=datetime.now(tz=UTC),
        )
    )
    # Never-classified regex 'other' → selected.
    db_session.add(_posting(target_company_id=tc.id, role_family="other"))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unclassified=True)

    assert resp.status_code == 200
    data = resp.json()
    # Only the legacy-version row + the never-classified row are processed;
    # the current-version 'other' is NOT re-bought.
    assert data["processed"] == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_llm_failure_skips_one_row(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM failure on call #3 of 5 → processed=5, skipped=1, changed=4."""
    from job_assist.main import app

    calls: list[int] = []
    _patch_classify(
        monkeypatch,
        ("product_management", "senior_pm"),
        call_counter=calls,
        fail_on_call=3,
    )

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    for _ in range(5):
        db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unclassified=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 5
    assert data["skipped"] == 1
    assert data["changed"] == 4


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_failed_row_preserves_original_values(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose LLM call fails keeps its original role_family + seniority."""
    from job_assist.main import app

    # Single posting — LLM always fails
    _patch_classify(monkeypatch, RuntimeError("always fails"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    posting = _posting(
        target_company_id=tc.id,
        role_family="other",
        seniority_level="unknown",
    )
    db_session.add(posting)
    await db_session.commit()
    posting_id = posting.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unclassified=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] == 1
    assert data["changed"] == 0

    # Verify the DB row was NOT changed
    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting_id))
    ).scalar_one()
    assert str(refreshed.role_family) == "other"
    assert str(refreshed.seniority_level) == "unknown"
    assert refreshed.classified_at is None
    assert refreshed.classifier_version is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_writes_classifier_metadata(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful sweep writes classifier_version + classified_at to the row."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    posting = _posting(target_company_id=tc.id)
    db_session.add(posting)
    await db_session.commit()
    posting_id = posting.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unclassified=True)

    assert resp.status_code == 200

    await db_session.refresh(posting)
    refreshed = (
        await db_session.execute(select(JobPosting).where(JobPosting.id == posting_id))
    ).scalar_one()
    assert refreshed.classifier_version == CLASSIFIER_VERSION
    assert refreshed.classified_at is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_distribution_keys_are_valid_enum_values(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distribution keys in the response are valid enum strings."""
    from job_assist.db.enums import RoleFamily, SeniorityLevel
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    db_session.add(_posting(target_company_id=tc.id))
    await db_session.commit()

    valid_families = {e.value for e in RoleFamily}
    valid_seniorities = {e.value for e in SeniorityLevel}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=5, only_unclassified=True)

    assert resp.status_code == 200
    dist = resp.json()["distribution"]

    for key in dist["role_family"]:
        assert key in valid_families, f"unexpected role_family key: {key!r}"
    for key in dist["seniority"]:
        assert key in valid_seniorities, f"unexpected seniority key: {key!r}"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_empty_table_returns_zeros(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweeping an empty table returns processed=0, changed=0, skipped=0."""
    from job_assist.main import app

    _patch_classify(monkeypatch, ("product_management", "senior_pm"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await _post_sweep(client, limit=50, only_unclassified=True)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 0
    assert data["changed"] == 0
    assert data["skipped"] == 0
    assert data["distribution"]["role_family"] == {}
    assert data["distribution"]["seniority"] == {}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_invalid_limit_422(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """limit=0 is rejected by Pydantic validation → 422."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/reclassify/sweep", json={"limit": 0})
    assert resp.status_code == 422


@_NEEDS_DB
@pytest.mark.asyncio
async def test_sweep_limit_too_large_422(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """limit=501 exceeds the 500 cap → 422."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/reclassify/sweep", json={"limit": 501})
    assert resp.status_code == 422
