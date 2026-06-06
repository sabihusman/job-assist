"""Manual application-status endpoint + resolved-status tab tests
(feat/manual-application-status Phase 1).

Exercises everything THROUGH the endpoints against a real DB:
  PUT /postings/{id}/status   — UPSERT the manual lifecycle stage
  GET /postings?state=applied — Applied tab = resolved IN (applied,interview,offer)
  GET /postings?state=rejected— Rejected tab = resolved == rejected
  GET /postings/{id}          — detail carries state.resolved_status + gmail hint

Contracts pinned:
  * UPSERT (one row per posting); applied_at stamped once and never moved.
  * Marking accepted/rejected DROPS a card out of Applied (the removal fix).
  * Manual rejected LANDS in Rejected.
  * Manual status is authoritative over the Gmail signal (COALESCE), and a
    company Gmail rejection on an *applied* card is an informational hint that
    does NOT move it out of Applied.
  * Gmail rejection still surfaces UNTOUCHED roles on Rejected (fallback).
  * updated_at auto-bumps on a status change.

DB-gated (need TEST_DATABASE_URL); run on CI's postgres.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import (
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


# ── client wiring (shared-session override, like the resume tests) ───────────


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


# ── fixture factories ────────────────────────────────────────────────────────


def _company(name: str | None = None) -> TargetCompany:
    return TargetCompany(
        name=name or f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="ashby",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(*, target_company_id: uuid.UUID | None, title: str | None = None) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    t = title or f"Senior Product Manager {suffix}"
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=t.lower(),
        raw_title=t,
        jd_text="JD body.",
        jd_text_hash=f"{'0' * 54}{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        role_family="product_management",  # type: ignore[arg-type]
        seniority_level="senior_pm",  # type: ignore[arg-type]
        remote_type="remote",
        fit_score=80,
    )


def _outcome(*, outcome_type: str, target_company_id: uuid.UUID | None) -> OutcomeEvent:
    suffix = uuid.uuid4().hex[:12]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        from_address=f"hr-{suffix}@example.com",
        from_domain="example.com",
        subject=f"Subject {suffix}",
        received_at=datetime.now(tz=UTC),
        outcome_type=outcome_type,  # type: ignore[arg-type]
        classifier_version="v_test",
        classifier_confidence=0.9,
        target_company_id=target_company_id,
    )


async def _make_applied_posting(db_session: Any) -> uuid.UUID:
    """A posting that has ENTERED the Applied funnel via the manual ``4`` key
    (posting_action='applied'), with a target company."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id)
    db_session.add(p)
    await db_session.flush()
    db_session.add(PostingAction(job_posting_id=p.id, action_type="applied"))
    await db_session.commit()
    return p.id


async def _list_titles(client: AsyncClient, **params: Any) -> set[str]:
    params.setdefault("per_company_cap", 0)
    params.setdefault("limit", 100)
    resp = await client.get("/postings", params=params)
    assert resp.status_code == 200, resp.text
    return {item["role"]["title"] for item in resp.json()["items"]}


# ── (1) UPSERT semantics ─────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_put_status_upserts_one_row_and_stamps_applied_at(db_session: Any) -> None:
    pid = await _make_applied_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r1 = await ac.put(f"/postings/{pid}/status", json={"status": "applied"})
            assert r1.status_code == 200, r1.text
            body1 = r1.json()
            assert body1["status"] == "applied"
            assert body1["applied_at"] is not None
            first_applied_at = body1["applied_at"]

            # Second PUT updates in place (no second row) and does NOT move
            # applied_at — it anchors Phase 2's badge.
            r2 = await ac.put(f"/postings/{pid}/status", json={"status": "interview"})
            assert r2.status_code == 200, r2.text
            body2 = r2.json()
            assert body2["status"] == "interview"
            assert body2["applied_at"] == first_applied_at
    finally:
        await _drop_override()

    from sqlalchemy import func, select

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(ApplicationState)
            .where(ApplicationState.job_posting_id == pid)
        )
    ).scalar_one()
    assert count == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_put_status_404_unknown_posting(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            r = await ac.put(f"/postings/{uuid.uuid4()}/status", json={"status": "applied"})
            assert r.status_code == 404
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_put_status_422_out_of_vocabulary(db_session: Any) -> None:
    pid = await _make_applied_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            r = await ac.put(f"/postings/{pid}/status", json={"status": "ghosted"})
            assert r.status_code == 422
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_updated_at_bumps_on_status_change(db_session: Any) -> None:
    pid = await _make_applied_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            await ac.put(f"/postings/{pid}/status", json={"status": "applied"})
            await ac.put(f"/postings/{pid}/status", json={"status": "interview"})
    finally:
        await _drop_override()

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(ApplicationState).where(ApplicationState.job_posting_id == pid)
        )
    ).scalar_one()
    # created_at stamped on the first PUT's transaction; updated_at on the
    # second PUT's (a later transaction) → strictly greater.
    assert row.updated_at > row.created_at


# ── (2) resolved_status drives the tabs ──────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_interview_keeps_card_in_applied(db_session: Any) -> None:
    pid = await _make_applied_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            detail = (await ac.get(f"/postings/{pid}")).json()
            title = detail["role"]["title"]
            await ac.put(f"/postings/{pid}/status", json={"status": "interview"})
            applied = await _list_titles(ac, state="applied")
            assert title in applied
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_removes_from_applied_and_lands_in_rejected(db_session: Any) -> None:
    pid = await _make_applied_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            title = (await ac.get(f"/postings/{pid}")).json()["role"]["title"]
            # Before: an applied card is in Applied.
            assert title in await _list_titles(ac, state="applied")
            # Mark rejected → drops out of Applied, lands in Rejected.
            await ac.put(f"/postings/{pid}/status", json={"status": "rejected"})
            assert title not in await _list_titles(ac, state="applied")
            assert title in await _list_titles(ac, state="rejected")

            # Detail reflects the resolved status.
            detail = (await ac.get(f"/postings/{pid}")).json()
            assert detail["state"]["resolved_status"] == "rejected"
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_accepted_removes_from_applied_without_landing_in_rejected(
    db_session: Any,
) -> None:
    pid = await _make_applied_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            title = (await ac.get(f"/postings/{pid}")).json()["role"]["title"]
            await ac.put(f"/postings/{pid}/status", json={"status": "accepted"})
            assert title not in await _list_titles(ac, state="applied")
            assert title not in await _list_titles(ac, state="rejected")
    finally:
        await _drop_override()


# ── (3) manual is authoritative; Gmail is an informational hint ──────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_gmail_rejection_on_applied_card_is_hint_not_mover(db_session: Any) -> None:
    """An applied card whose company has a Gmail rejection (but no manual
    status) STAYS in Applied — Gmail is a hint there, not authoritative — and
    the hint flag is surfaced. Then a manual 'rejected' is what moves it."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id)
    db_session.add(p)
    await db_session.flush()
    db_session.add(PostingAction(job_posting_id=p.id, action_type="applied"))
    db_session.add(_outcome(outcome_type="rejection_post_screen", target_company_id=tc.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            detail = (await ac.get(f"/postings/{p.id}")).json()
            title = detail["role"]["title"]
            # entered_applied precedence → resolved 'applied', hint flagged.
            assert detail["state"]["resolved_status"] == "applied"
            assert detail["state"]["gmail_rejection_hint"] is True
            assert title in await _list_titles(ac, state="applied")
            # Gmail alone did NOT put it in Rejected.
            assert title not in await _list_titles(ac, state="rejected")

            # Manual rejected is authoritative → moves it.
            await ac.put(f"/postings/{p.id}/status", json={"status": "rejected"})
            assert title not in await _list_titles(ac, state="applied")
            assert title in await _list_titles(ac, state="rejected")
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_gmail_rejection_untouched_role_is_hint_only_not_rejected(db_session: Any) -> None:
    """An UNTOUCHED role (no posting_action, no manual status) at a company
    with a Gmail rejection does NOT surface on Rejected.

    Company-level Gmail signals can't be linked to a specific posting
    (outcome_event.job_posting_id is always NULL), so treating them as tab
    membership fanned them across every role at the company — passed and
    never-touched roles included. Posting-specific fix: the Gmail rejection
    is now an INFORMATIONAL hint only (gmail_rejection_hint=True), never a
    mover. resolved_status stays NULL until the operator manually statuses it.
    """
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id)
    db_session.add(p)
    db_session.add(_outcome(outcome_type="rejection_post_screen", target_company_id=tc.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            detail = (await ac.get(f"/postings/{p.id}")).json()
            title = detail["role"]["title"]
            # Hint surfaces, but membership does NOT.
            assert detail["state"]["resolved_status"] is None
            assert detail["state"]["gmail_rejection_hint"] is True
            assert title not in await _list_titles(ac, state="rejected")
            assert title not in await _list_titles(ac, state="applied")
    finally:
        await _drop_override()


@_NEEDS_DB
@pytest.mark.asyncio
async def test_manual_status_coalesce_overrides_computed(db_session: Any) -> None:
    """COALESCE puts manual status first: a manually 'offer' card at a company
    with a Gmail rejection resolves to 'offer' (in Applied), not 'rejected'."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id)
    db_session.add(p)
    db_session.add(_outcome(outcome_type="rejection_post_screen", target_company_id=tc.id))
    await db_session.commit()

    ac = await _client(db_session)
    try:
        async with ac:
            title = (await ac.get(f"/postings/{p.id}")).json()["role"]["title"]
            await ac.put(f"/postings/{p.id}/status", json={"status": "offer"})
            detail = (await ac.get(f"/postings/{p.id}")).json()
            assert detail["state"]["resolved_status"] == "offer"
            assert title in await _list_titles(ac, state="applied")
            assert title not in await _list_titles(ac, state="rejected")
    finally:
        await _drop_override()
