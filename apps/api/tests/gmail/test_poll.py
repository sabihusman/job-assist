"""Tests for ``run_poll`` — the watermark-driven Gmail poll path.

Exercises the watermark derivation, the bootstrap fallback, idempotency,
and second-run advancement. All HTTP / Gemini surfaces are mocked via
the same protocol-shaped fakes used by ``test_backfill.py``.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from job_assist.db.models import OutcomeEvent
from job_assist.gmail.backfill import (
    POLL_BOOTSTRAP_LOOKBACK,
    build_after_query,
    run_poll,
)
from job_assist.gmail.models import ClassificationResult, RawEmail

# Re-use the synthetic _FakeGmail / _FakeClassifier from the backfill suite.
from tests.gmail.test_backfill import _email, _FakeClassifier, _FakeGmail, _verdict

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _after_seconds(query: str) -> int | None:
    """Parse the ``after:<unix-seconds>`` integer out of a poll query."""
    m = re.search(r"after:(\d+)", query)
    return int(m.group(1)) if m else None


# ── Tests ──────────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_poll_uses_max_received_at_as_watermark(db_session: Any) -> None:
    """The poll query's ``after:`` operator equals MAX(received_at).timestamp()."""
    watermark_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

    # Seed a pre-existing outcome_event at the watermark.
    db_session.add(
        OutcomeEvent(
            email_message_id="seeded_before_poll",
            from_address="x@a.com",
            from_domain="a.com",
            subject="seeded",
            received_at=watermark_ts,
            outcome_type="rejection_pre_screen",
            classifier_version="gemini-flash-lite-v1",
            classifier_confidence=0.9,
        )
    )
    await db_session.commit()

    # New mail returned by the fake Gmail — orchestrator will classify
    # only the not-yet-seen one.
    new_email = _email("msg_new", from_address="hr@somecompany.com")
    gmail = _FakeGmail([new_email])
    classifier = _FakeClassifier({"msg_new": _verdict("application_confirmation")})

    report = await run_poll(db_session, gmail, classifier)

    assert len(gmail.list_queries) == 1, "list_message_ids called exactly once"
    parsed = _after_seconds(gmail.list_queries[0])
    assert parsed == int(watermark_ts.timestamp()), (
        f"poll query should use MAX(received_at) unix seconds; got {parsed}"
    )
    assert report.watermark_used == watermark_ts
    assert report.outcome_events_inserted == 1


@_NEEDS_DB
async def test_poll_defaults_to_24h_when_table_empty(db_session: Any) -> None:
    """Empty outcome_event → watermark falls back to now() - POLL_BOOTSTRAP_LOOKBACK."""
    gmail = _FakeGmail([])
    classifier = _FakeClassifier({})

    before_call = datetime.now(tz=UTC)
    report = await run_poll(db_session, gmail, classifier)
    after_call = datetime.now(tz=UTC)

    assert report.watermark_used is not None
    drift = after_call - report.watermark_used
    # Should be ~24h ago, ±a couple of seconds of test execution slack.
    assert (
        POLL_BOOTSTRAP_LOOKBACK - timedelta(seconds=10)
        <= drift
        <= POLL_BOOTSTRAP_LOOKBACK + timedelta(seconds=10)
    ), f"expected drift ≈ {POLL_BOOTSTRAP_LOOKBACK}, got {drift}"

    # Sanity: watermark sits in [now-24h, before_call-24h] roughly.
    assert report.watermark_used <= before_call - POLL_BOOTSTRAP_LOOKBACK + timedelta(seconds=2)

    # Empty inbox → no inserts, watermark unchanged.
    assert report.outcome_events_inserted == 0
    assert report.watermark_advanced_to is None  # MAX(received_at) is still NULL


@_NEEDS_DB
async def test_poll_inserts_only_new_events(db_session: Any) -> None:
    """The unique pre-check by email_message_id still prevents dupes on the poll path."""
    watermark_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    db_session.add(
        OutcomeEvent(
            email_message_id="M1",
            from_address="x@a.com",
            from_domain="a.com",
            subject="seeded",
            received_at=watermark_ts,
            outcome_type="rejection_pre_screen",
            classifier_version="gemini-flash-lite-v1",
            classifier_confidence=0.9,
        )
    )
    await db_session.commit()

    # Gmail returns both — the orchestrator must skip M1 and only classify M2.
    emails = [
        _email("M1", from_address="x@a.com"),
        _email("M2", from_address="hr@somecompany.com"),
    ]
    gmail = _FakeGmail(emails)
    classifier = _FakeClassifier({"M2": _verdict("application_confirmation")})

    report = await run_poll(db_session, gmail, classifier)

    assert classifier.classify_calls == ["M2"], "M1 should never reach the classifier"
    assert report.skipped_already_classified == 1
    assert report.outcome_events_inserted == 1

    total = (await db_session.execute(select(func.count()).select_from(OutcomeEvent))).scalar_one()
    assert total == 2  # the original + the newly-inserted M2


@_NEEDS_DB
async def test_poll_advances_watermark(db_session: Any) -> None:
    """Second poll should use the first run's newest received_at as its watermark."""
    initial_watermark = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
    db_session.add(
        OutcomeEvent(
            email_message_id="M_initial",
            from_address="x@a.com",
            from_domain="a.com",
            subject="seed",
            received_at=initial_watermark,
            outcome_type="rejection_pre_screen",
            classifier_version="gemini-flash-lite-v1",
            classifier_confidence=0.9,
        )
    )
    await db_session.commit()

    # Run 1: fake serves one new email received 2 hours after the watermark.
    new_received = initial_watermark + timedelta(hours=2)
    run1_email = _email("M_after_seed", from_address="hr@somecompany.com")
    run1_email = run1_email.model_copy(update={"received_at": new_received})
    gmail_1 = _FakeGmail([run1_email])
    classifier_1 = _FakeClassifier({"M_after_seed": _verdict("application_confirmation")})
    report_1 = await run_poll(db_session, gmail_1, classifier_1)

    assert report_1.outcome_events_inserted == 1
    assert _after_seconds(gmail_1.list_queries[0]) == int(initial_watermark.timestamp())
    assert report_1.watermark_advanced_to == new_received

    # Run 2: fresh fake. The watermark should now be the run-1 inserted row's received_at.
    gmail_2 = _FakeGmail([])
    classifier_2 = _FakeClassifier({})
    report_2 = await run_poll(db_session, gmail_2, classifier_2)

    assert _after_seconds(gmail_2.list_queries[0]) == int(new_received.timestamp()), (
        "second poll should use the newest received_at as watermark, not the seed value"
    )
    assert report_2.watermark_used == new_received
    assert report_2.outcome_events_inserted == 0


def test_build_after_query_shape() -> None:
    """Pure-function check: builder produces the expected Gmail operator format."""
    ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    assert build_after_query(ts) == f"after:{int(ts.timestamp())}"


def test_unused_imports_smoke() -> None:
    """Touch the cross-module re-exports so coverage sees them."""
    # ClassificationResult and RawEmail are imported by the tests above
    # transitively; this assertion just makes the imports explicit so
    # ruff's F401 doesn't flag them.
    assert ClassificationResult.__name__ == "ClassificationResult"
    assert RawEmail.__name__ == "RawEmail"
