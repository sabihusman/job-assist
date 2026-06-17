"""Tests for GET /admin/diagnostics/triples (feat/triple-aware-apply, 1b).

The read-only per-applied-posting (posting + resume + outcome) corpus-
completeness view. Membership is ``resolved_status = 'applied'`` (option a:
latest-action, applied-THEN-reset excluded). DB-backed; skipped without
TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import (
    ApplicationResume,
    ApplicationState,
    JobPosting,
    OutcomeEvent,
    PostingAction,
    TargetCompany,
)

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


# ── Factories ────────────────────────────────────────────────────────────────


async def _posting(db_session: Any, *, company: str = "TripleCo", title: str = "pm") -> uuid.UUID:
    tc = TargetCompany(
        name=company,
        tier=1,
        ats="greenhouse",
        ats_handle=f"h-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(tc)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    jp = JobPosting(
        canonical_company_name=company,
        target_company_id=tc.id,
        normalized_title=title,
        raw_title=title.title(),
        remote_type="remote",
        role_family="product_management",
        seniority_level="senior_pm",
        jd_text="JD.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        fit_score=77,
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(jp)
    await db_session.commit()
    return jp.id


def _action(
    pid: uuid.UUID, action_type: str, *, created_at: datetime | None = None
) -> PostingAction:
    return PostingAction(
        job_posting_id=pid,
        action_type=action_type,
        created_at=created_at or datetime.now(tz=UTC),
    )


def _outcome(pid: uuid.UUID | None, outcome_type: str) -> OutcomeEvent:
    return OutcomeEvent(
        job_posting_id=pid,
        email_message_id=f"msg-{uuid.uuid4().hex}",
        from_address="recruiter@co.com",
        from_domain="co.com",
        subject="re: your application",
        received_at=datetime.now(tz=UTC),
        outcome_type=outcome_type,
        classifier_version="test",
    )


async def _get_triples(db_session: Any) -> dict[str, Any]:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.get("/admin/diagnostics/triples")
    finally:
        await _drop_override()
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Tests ────────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_triples_applied_with_resume_and_outcome(db_session: Any) -> None:
    """A posting applied (via posting_action) WITH a resume AND a linked outcome
    assembles a complete triple with the proposed columns."""
    pid = await _posting(db_session, company="Goldman", title="tb pm")
    db_session.add(_action(pid, "applied"))
    db_session.add(
        ApplicationResume(job_posting_id=pid, file_name="gs.docx", resume_text="full text")
    )
    db_session.add(_outcome(pid, "offer"))
    await db_session.commit()

    body = await _get_triples(db_session)
    row = next(t for t in body["triples"] if t["posting"]["id"] == str(pid))
    assert row["posting"]["company"] == "Goldman"
    assert row["posting"]["title"] == "tb pm"
    assert row["posting"]["fit_score"] == 77
    assert row["resume"]["file_name"] == "gs.docx"
    assert row["resume"]["has_resume_text"] is True
    assert row["resume_attached"] is True
    assert row["outcome"]["outcome_type"] == "offer"
    assert row["outcome"]["received_at"] is not None
    assert body["summary"]["complete_triples"] >= 1


@_NEEDS_DB
async def test_triples_applied_no_resume_is_gap(db_session: Any) -> None:
    """Applied but no resume → in the view with resume=None / resume_attached
    false (the standing gap list)."""
    pid = await _posting(db_session)
    db_session.add(_action(pid, "applied"))
    await db_session.commit()

    body = await _get_triples(db_session)
    row = next(t for t in body["triples"] if t["posting"]["id"] == str(pid))
    assert row["resume"] is None
    assert row["resume_attached"] is False
    assert row["outcome"] is None
    assert body["summary"]["applied_no_resume"] >= 1


@_NEEDS_DB
async def test_triples_outcome_null_when_unlinked(db_session: Any) -> None:
    """has_resume_text reflects empty text; outcome null when no linked event."""
    pid = await _posting(db_session)
    db_session.add(_action(pid, "applied"))
    db_session.add(ApplicationResume(job_posting_id=pid, file_name="x.docx", resume_text=""))
    await db_session.commit()

    body = await _get_triples(db_session)
    row = next(t for t in body["triples"] if t["posting"]["id"] == str(pid))
    assert row["resume_attached"] is True
    assert row["resume"]["has_resume_text"] is False
    assert row["outcome"] is None


@_NEEDS_DB
async def test_triples_manual_status_applied_included(db_session: Any) -> None:
    """A posting whose membership comes from manual application_state.status =
    'applied' (no posting_action) is in the view — exercises the COALESCE."""
    pid = await _posting(db_session)
    db_session.add(ApplicationState(job_posting_id=pid, status="applied"))
    await db_session.commit()

    body = await _get_triples(db_session)
    assert any(t["posting"]["id"] == str(pid) for t in body["triples"])


@_NEEDS_DB
async def test_triples_applied_then_reset_excluded(db_session: Any) -> None:
    """OPTION (a) LOCK: a posting applied THEN reset (latest action 'reset', no
    manual status) resolves to NULL and MUST NOT appear in the triple view.
    Guards against future drift back to a raw EXISTS posting_action='applied'."""
    pid = await _posting(db_session)
    base = datetime.now(tz=UTC)
    db_session.add(_action(pid, "applied", created_at=base - timedelta(minutes=2)))
    db_session.add(_action(pid, "reset", created_at=base))
    await db_session.commit()

    body = await _get_triples(db_session)
    assert all(t["posting"]["id"] != str(pid) for t in body["triples"])
