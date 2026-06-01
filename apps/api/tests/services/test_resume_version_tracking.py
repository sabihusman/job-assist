"""Tests for resume-version tracking (feat/resume-version-tracking).

Covers: the ResumeVersionCreate schema validator (pure), the
resume_version_id CHECK guard in record_action (DB-gated), the create +
list endpoints (DB-gated), and the analytics aggregation incl. the
company-level ambiguity flag (DB-gated).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.enums import ActionType
from job_assist.db.models import JobPosting, OutcomeEvent, ResumeVersion, TargetCompany
from job_assist.schemas.resume_version import ResumeVersionCreate

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure: schema validator ───────────────────────────────────────────────────


def test_resume_version_create_strips_and_requires_label() -> None:
    assert ResumeVersionCreate(label="  betterment-trust-v1  ").label == "betterment-trust-v1"
    with pytest.raises(ValueError, match="label must be non-empty"):
        ResumeVersionCreate(label="   ")


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _company(name: str | None = None) -> TargetCompany:
    return TargetCompany(
        name=name or f"Co-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"h-{uuid.uuid4().hex[:6]}",
    )


def _posting(tc_id: uuid.UUID) -> JobPosting:
    now = datetime.now(tz=UTC)
    sfx = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="Co",
        target_company_id=tc_id,
        normalized_title="product manager",
        raw_title="Product Manager",
        jd_text="jd",
        jd_text_hash=f"{'0' * 54}{sfx}",
        content_hash=f"hash-{sfx}",
        first_seen_at=now,
        last_seen_at=now,
        role_family="product_management",  # type: ignore[arg-type]
        remote_type="remote",
    )


def _outcome(tc_id: uuid.UUID, outcome_type: str) -> OutcomeEvent:
    sfx = uuid.uuid4().hex[:12]
    return OutcomeEvent(
        email_message_id=f"m-{sfx}",
        from_address=f"hr-{sfx}@example.com",
        from_domain="example.com",
        subject="s",
        received_at=datetime.now(tz=UTC),
        outcome_type=outcome_type,  # type: ignore[arg-type]
        classifier_version="v",
        classifier_confidence=0.9,
        target_company_id=tc_id,
    )


# ── record_action: resume_version_id CHECK guard ─────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_record_action_tags_resume_on_applied(db_session: Any) -> None:
    from job_assist.services.posting_actions import record_action

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(tc.id)
    rv = ResumeVersion(label=f"v-{uuid.uuid4().hex[:6]}", angle="trust")
    db_session.add_all([p, rv])
    await db_session.commit()

    row = await record_action(db_session, p.id, ActionType.applied, resume_version_id=rv.id)
    assert row.resume_version_id == rv.id


@_NEEDS_DB
@pytest.mark.asyncio
async def test_record_action_rejects_resume_on_non_applied(db_session: Any) -> None:
    """The service raises 422-shaped ValueError when tagging a resume on
    a non-applied action (mirrors the DB CHECK)."""
    from job_assist.services.posting_actions import record_action

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(tc.id)
    rv = ResumeVersion(label=f"v-{uuid.uuid4().hex[:6]}")
    db_session.add_all([p, rv])
    await db_session.commit()

    with pytest.raises(ValueError, match="only valid with action_type='applied'"):
        await record_action(db_session, p.id, ActionType.interested, resume_version_id=rv.id)


# ── Endpoints: create + list ─────────────────────────────────────────────────


async def _client(db_session: Any) -> AsyncClient:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@_NEEDS_DB
@pytest.mark.asyncio
async def test_create_and_list_resume_versions(db_session: Any) -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    label = f"betterment-trust-{uuid.uuid4().hex[:6]}"
    ac = await _client(db_session)
    try:
        async with ac:
            r = await ac.post(
                "/admin/resume-versions",
                json={"label": label, "angle": "trust/compliance", "notes": "n"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["label"] == label
            # Duplicate label → 409.
            r2 = await ac.post("/admin/resume-versions", json={"label": label})
            assert r2.status_code == 409
            # List shows it.
            r3 = await ac.get("/resume-versions")
            assert r3.status_code == 200
            assert label in {it["label"] for it in r3.json()["items"]}
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── Analytics aggregation + ambiguity flag ───────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_resume_analytics_by_version_and_ambiguity(db_session: Any) -> None:
    """Two versions, two companies. Company A: one version, rejection.
    Company B: BOTH versions applied (ambiguous), confirmation."""
    from job_assist.services.posting_actions import record_action
    from job_assist.services.resume_analytics import resume_analytics

    rv1 = ResumeVersion(label=f"v1-{uuid.uuid4().hex[:6]}", angle="trust")
    rv2 = ResumeVersion(label=f"v2-{uuid.uuid4().hex[:6]}", angle="growth")
    co_a = _company("CoA")
    co_b = _company("CoB")
    db_session.add_all([rv1, rv2, co_a, co_b])
    await db_session.flush()
    pa = _posting(co_a.id)  # company A, one role
    pb1 = _posting(co_b.id)  # company B, role 1
    pb2 = _posting(co_b.id)  # company B, role 2
    db_session.add_all([pa, pb1, pb2])
    await db_session.flush()

    # A: applied with rv1; A rejected.
    await record_action(db_session, pa.id, ActionType.applied, resume_version_id=rv1.id)
    db_session.add(_outcome(co_a.id, "rejection_post_screen"))
    # B: applied to role1 with rv1, role2 with rv2 → ambiguous; B confirmed.
    await record_action(db_session, pb1.id, ActionType.applied, resume_version_id=rv1.id)
    await record_action(db_session, pb2.id, ActionType.applied, resume_version_id=rv2.id)
    db_session.add(_outcome(co_b.id, "application_confirmation"))
    await db_session.commit()

    out = await resume_analytics(db_session)

    by = {r["label"]: r for r in out["by_version"]}
    # rv1: 2 applications (A + B-role1), 2 companies, 1 rejected (A), 1 confirmed (B).
    assert by[rv1.label]["applications"] == 2
    assert by[rv1.label]["companies_rejected"] == 1
    assert by[rv1.label]["companies_confirmed"] == 1
    # rv2: 1 application (B-role2), company B confirmed, not rejected.
    assert by[rv2.label]["applications"] == 1
    assert by[rv2.label]["companies_confirmed"] == 1
    assert by[rv2.label]["companies_rejected"] == 0

    # Company B got 2 distinct versions → flagged ambiguous; A is not.
    ambig_ids = {a["company_id"] for a in out["ambiguous_companies"]}
    assert str(co_b.id) in ambig_ids
    assert str(co_a.id) not in ambig_ids
    # The honest caveat is surfaced in the payload.
    assert "company level" in out["attribution_note"].lower()
