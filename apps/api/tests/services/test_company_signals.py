"""DB-gated tests for per-company repeat signals (feat/repeat-signal-flags)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from job_assist.db.models import OutcomeEvent, TargetCompany
from job_assist.services.company_signals import compute_repeat_signals

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _company(name: str) -> TargetCompany:
    return TargetCompany(name=name, ats="unknown")


def _outcome(
    *,
    company_id: uuid.UUID | None,
    outcome_type: str,
    minutes: int = 0,
    thread: str | None = None,
) -> OutcomeEvent:
    suffix = uuid.uuid4().hex[:10]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        email_thread_id=thread,
        from_address="recruiter@example.com",
        from_domain="example.com",
        subject="Re: your application",
        received_at=_BASE + timedelta(minutes=minutes),
        outcome_type=outcome_type,
        classifier_version="test-v1",
        target_company_id=company_id,
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_multiple_rejections_flagged(db_session: Any) -> None:
    co = _company("RejectCo")
    db_session.add(co)
    await db_session.flush()
    for i in range(3):
        db_session.add(
            _outcome(company_id=co.id, outcome_type="rejection_pre_screen", thread=f"t{i}")
        )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals[str(co.id)] == {"rejections": 3, "active_apps": 0}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_multiple_active_apps_flagged(db_session: Any) -> None:
    co = _company("AliveCo")
    db_session.add(co)
    await db_session.flush()
    # Two distinct alive threads.
    db_session.add(_outcome(company_id=co.id, outcome_type="application_confirmation", thread="a"))
    db_session.add(_outcome(company_id=co.id, outcome_type="recruiter_screen_invite", thread="b"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals[str(co.id)] == {"rejections": 0, "active_apps": 2}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_single_signal_below_threshold_omitted(db_session: Any) -> None:
    co = _company("OnceCo")
    db_session.add(co)
    await db_session.flush()
    db_session.add(_outcome(company_id=co.id, outcome_type="rejection_pre_screen", thread="t"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert str(co.id) not in signals  # one rejection, one app — below 2 on both


@_NEEDS_DB
@pytest.mark.asyncio
async def test_latest_wins_excludes_rejected_thread_from_active(db_session: Any) -> None:
    """A thread that ends in rejection is NOT counted as a still-alive app, even
    though it began with a confirmation (latest-wins, mirroring the Pipeline)."""
    co = _company("MixedCo")
    db_session.add(co)
    await db_session.flush()
    # Thread 1: confirmation then a LATER rejection → rejected, not alive.
    db_session.add(
        _outcome(company_id=co.id, outcome_type="application_confirmation", thread="t1", minutes=0)
    )
    db_session.add(
        _outcome(company_id=co.id, outcome_type="rejection_post_screen", thread="t1", minutes=10)
    )
    # Threads 2 & 3: still alive.
    db_session.add(_outcome(company_id=co.id, outcome_type="application_confirmation", thread="t2"))
    db_session.add(_outcome(company_id=co.id, outcome_type="offer", thread="t3"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    # active = t2 + t3 (t1 flipped to rejected); rejections = the one t1 reject.
    assert signals[str(co.id)] == {"rejections": 1, "active_apps": 2}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_unlinked_outcomes_not_counted(db_session: Any) -> None:
    # No target_company_id → cannot be attributed to a company → never flagged.
    for i in range(3):
        db_session.add(
            _outcome(company_id=None, outcome_type="rejection_pre_screen", thread=f"u{i}")
        )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals == {}
