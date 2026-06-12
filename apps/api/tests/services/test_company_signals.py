"""DB-gated tests for company-level application awareness
(feat/company-app-awareness).

The signal map is now keyed by NORMALIZED company name and counts BOTH linked
(``target_company_id``) and unlinked outcomes (name extracted from the subject),
with an ambiguity guard that suppresses subset-colliding names.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from job_assist.db.models import Contact, OutcomeEvent, TargetCompany
from job_assist.services.company_signals import compute_repeat_signals

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _company(name: str) -> TargetCompany:
    return TargetCompany(name=name, ats="unknown")


def _contact(employer: str | None, *, archived: bool = False) -> Contact:
    """Minimal valid contact (reachability CHECK needs email or LinkedIn)."""
    suffix = uuid.uuid4().hex[:8]
    return Contact(
        first_name="Test",
        last_name=f"Alum{suffix}",
        email_primary=f"alum-{suffix}@example.com",
        current_employer=employer,
        source_type="tippie_alumni",
        archived_at=_BASE if archived else None,
    )


def _outcome(
    *,
    company_id: uuid.UUID | None,
    outcome_type: str,
    minutes: int = 0,
    thread: str | None = None,
    subject: str = "Re: your application",
) -> OutcomeEvent:
    suffix = uuid.uuid4().hex[:10]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        email_thread_id=thread,
        from_address="recruiter@example.com",
        from_domain="example.com",
        subject=subject,
        received_at=_BASE + timedelta(minutes=minutes),
        outcome_type=outcome_type,
        classifier_version="test-v1",
        target_company_id=company_id,
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_multiple_rejections_flagged_by_name(db_session: Any) -> None:
    co = _company("RejectCo")
    db_session.add(co)
    await db_session.flush()
    for i in range(3):
        db_session.add(
            _outcome(company_id=co.id, outcome_type="rejection_pre_screen", thread=f"t{i}")
        )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["rejectco"]["rejections"] == 3
    assert signals["rejectco"]["active_apps"] == 0
    assert signals["rejectco"]["display_name"] == "RejectCo"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_multiple_active_apps_flagged(db_session: Any) -> None:
    co = _company("AliveCo")
    db_session.add(co)
    await db_session.flush()
    db_session.add(_outcome(company_id=co.id, outcome_type="application_confirmation", thread="a"))
    db_session.add(_outcome(company_id=co.id, outcome_type="recruiter_screen_invite", thread="b"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["aliveco"]["active_apps"] == 2
    assert signals["aliveco"]["rejections"] == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_single_count_is_returned(db_session: Any) -> None:
    """Unlike the old >=2 threshold, a SINGLE app/rejection is now returned - the
    frontend renders 1-2 as a neutral badge and only >=3 active as amber."""
    co = _company("OnceCo")
    db_session.add(co)
    await db_session.flush()
    db_session.add(_outcome(company_id=co.id, outcome_type="rejection_pre_screen", thread="t"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["onceco"] == {
        "rejections": 1,
        "active_apps": 0,
        "contact_count": 0,
        "display_name": "OnceCo",
    }


@_NEEDS_DB
@pytest.mark.asyncio
async def test_latest_wins_excludes_rejected_thread_from_active(db_session: Any) -> None:
    co = _company("MixedCo")
    db_session.add(co)
    await db_session.flush()
    db_session.add(
        _outcome(company_id=co.id, outcome_type="application_confirmation", thread="t1", minutes=0)
    )
    db_session.add(
        _outcome(company_id=co.id, outcome_type="rejection_post_screen", thread="t1", minutes=10)
    )
    db_session.add(_outcome(company_id=co.id, outcome_type="application_confirmation", thread="t2"))
    db_session.add(_outcome(company_id=co.id, outcome_type="offer", thread="t3"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["mixedco"]["rejections"] == 1
    assert signals["mixedco"]["active_apps"] == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_unlinked_outcomes_counted_by_subject(db_session: Any) -> None:
    """The unlinked majority IS counted now — the company is extracted from the
    subject ("applying to <X>"), capturing what the id-keyed version missed."""
    for i in range(2):
        db_session.add(
            _outcome(
                company_id=None,
                outcome_type="rejection_pre_screen",
                thread=f"u{i}",
                subject="Thank you for applying to Wealthsimple",
            )
        )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["wealthsimple"]["rejections"] == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_linked_and_unlinked_merge_under_one_name(db_session: Any) -> None:
    """A linked event and an unlinked subject-extracted event for the SAME
    company collapse to one normalized key (linked name "Stripe, Inc." and
    subject "applying to Stripe" both → "stripe")."""
    co = _company("Stripe, Inc.")
    db_session.add(co)
    await db_session.flush()
    db_session.add(_outcome(company_id=co.id, outcome_type="application_confirmation", thread="L"))
    db_session.add(
        _outcome(
            company_id=None,
            outcome_type="application_confirmation",
            thread="U",
            subject="Thanks for applying to Stripe",
        )
    )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["stripe"]["active_apps"] == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_ambiguous_subset_names_suppressed(db_session: Any) -> None:
    """No-false-badge guard: "John Hancock" and "Manulife John Hancock" — one a
    token-subset of the other — are BOTH suppressed rather than risk a wrong
    count."""
    jh = _company("John Hancock")
    manulife = _company("Manulife John Hancock")
    db_session.add_all([jh, manulife])
    await db_session.flush()
    db_session.add(_outcome(company_id=jh.id, outcome_type="application_confirmation", thread="j1"))
    db_session.add(_outcome(company_id=jh.id, outcome_type="application_confirmation", thread="j2"))
    db_session.add(
        _outcome(company_id=manulife.id, outcome_type="rejection_pre_screen", thread="m1")
    )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert "john hancock" not in signals
    assert "manulife john hancock" not in signals


@_NEEDS_DB
@pytest.mark.asyncio
async def test_generic_subject_unlinked_not_counted(db_session: Any) -> None:
    """An unlinked outcome whose subject yields no company name can't be
    attributed → not counted (no fan-out)."""
    db_session.add(
        _outcome(
            company_id=None,
            outcome_type="rejection_pre_screen",
            thread="g",
            subject="Update on your application",
        )
    )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals == {}


# ── feat/warm-path-badge ──────────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_contact_counts_collapse_by_normalized_name(db_session: Any) -> None:
    """Contacts at 'John Deere' / ' john deere ' / 'John Deere Inc.' collapse to
    one normalized key with contact_count=3; a contact-ONLY company is emitted
    even with zero outcomes."""
    db_session.add_all(
        [
            _contact("John Deere"),
            _contact(" john deere "),
            _contact("John Deere Inc."),
        ]
    )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    sig = signals["john deere"]
    assert sig["contact_count"] == 3
    assert sig["rejections"] == 0
    assert sig["active_apps"] == 0
    assert sig["display_name"] == "John Deere"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_archived_and_employerless_contacts_excluded(db_session: Any) -> None:
    db_session.add_all(
        [
            _contact("Collins Aerospace"),
            _contact("Collins Aerospace", archived=True),  # archived → not counted
            _contact(None),  # no employer → can't attribute
            _contact("   "),  # whitespace employer → can't attribute
        ]
    )
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["collins aerospace"]["contact_count"] == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_contacts_merge_with_outcome_counts(db_session: Any) -> None:
    """A company with BOTH outcome history and contacts carries all three counts
    on one entry (same normalized key)."""
    co = _company("Athene")
    db_session.add(co)
    await db_session.flush()
    db_session.add(_outcome(company_id=co.id, outcome_type="application_confirmation", thread="a"))
    db_session.add(_contact("Athene"))
    db_session.add(_contact("ATHENE"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals["athene"] == {
        "rejections": 0,
        "active_apps": 1,
        "contact_count": 2,
        "display_name": "Athene",
    }


@_NEEDS_DB
@pytest.mark.asyncio
async def test_ambiguity_guard_spans_contact_and_outcome_names(db_session: Any) -> None:
    """No-false-badge guard over the UNION of sources: a CONTACT at 'John
    Hancock' and OUTCOMES at 'Manulife John Hancock' suppress each other —
    neither key is emitted."""
    manulife = _company("Manulife John Hancock")
    db_session.add(manulife)
    await db_session.flush()
    db_session.add(
        _outcome(company_id=manulife.id, outcome_type="rejection_pre_screen", thread="m")
    )
    db_session.add(_contact("John Hancock"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert "john hancock" not in signals
    assert "manulife john hancock" not in signals


@_NEEDS_DB
@pytest.mark.asyncio
async def test_vendor_employer_not_counted(db_session: Any) -> None:
    """An employer string that normalizes to a vendor/empty key is skipped."""
    db_session.add(_contact("greenhouse.io"))
    await db_session.commit()

    signals = await compute_repeat_signals(db_session)
    assert signals == {}


# ── fix(audit): deterministic display_name on tied counts ─────────────────────


@_NEEDS_DB
async def test_display_name_tiebreak_is_deterministic(db_session: Any) -> None:
    """Counter.most_common breaks ties by insertion (DB row) order, so
    equal-count name variants flipped the badge label between runs. Ties now
    resolve lexicographically: 'Acme' beats 'Acme Corp' at 1-1."""
    # Two contacts whose employers normalize to the same key with one raw
    # spelling each — a guaranteed 1-1 tie.
    db_session.add(_contact("Acme Corp"))
    db_session.add(_contact("Acme"))
    await db_session.commit()

    from job_assist.services.company_signals import compute_repeat_signals

    for _ in range(3):  # stable across repeated computes
        signals = await compute_repeat_signals(db_session)
        assert signals["acme"]["display_name"] == "Acme"
        assert signals["acme"]["contact_count"] == 2
