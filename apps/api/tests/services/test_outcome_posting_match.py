"""Pure tests for the Gmail-outcome → posting matcher (cross-link).

The matching logic is deterministic token overlap — no DB needed. These lock
the no-fanout guarantees: a confident single role match links; a generic /
ambiguous email at a multi-posting company links nothing.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from job_assist.db.models import JobPosting, OutcomeEvent, TargetCompany
from job_assist.services.outcome_posting_match import (
    OUTCOME_LINK_CLOSED_WINDOW_DAYS,
    _best_posting_id,
    link_outcomes_to_postings,
    role_match_score,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _posting(title: str) -> JobPosting:
    # The declarative constructor sets _sa_instance_state; NOT NULL columns are
    # only enforced at flush, and the uuid default only applies at flush, so set
    # id explicitly.
    return JobPosting(id=uuid.uuid4(), normalized_title=title)


def _event(subject: str, snippet: str | None = None) -> OutcomeEvent:
    return OutcomeEvent(subject=subject, raw_snippet=snippet)


# ── role_match_score ─────────────────────────────────────────────────────────


def test_role_match_score_high_for_specific_role() -> None:
    s = role_match_score(
        "Thank you for applying to the Product Manager, Risk Analytics role",
        None,
        "product manager, risk analytics",
    )
    assert s >= 0.6


def test_role_match_score_zero_when_only_generic_words_overlap() -> None:
    # "manager"/"senior" are generic — a single generic overlap must not match.
    assert (
        role_match_score("Thanks for applying, Senior Manager", None, "senior product manager")
        == 0.0
    )


def test_role_match_score_zero_when_subject_has_no_role() -> None:
    # Confirmation with no role in it → cannot disambiguate → 0.
    assert (
        role_match_score("Thank you for applying to Capital One", None, "product manager, pulse")
        == 0.0
    )


# ── _best_posting_id (the no-fanout core) ────────────────────────────────────


def test_links_single_clear_role_match() -> None:
    ev = _event("Your application for the Risk Analytics Product Manager position")
    candidates = [
        _posting("product manager, risk analytics"),
        _posting("product manager, payments network"),
        _posting("senior product manager, fraud"),
    ]
    picked = _best_posting_id(ev, candidates)
    assert picked == candidates[0].id  # the risk-analytics role, not its siblings


def test_no_link_when_ambiguous_generic_subject_at_multi_posting_company() -> None:
    # A bare "Product Manager" confirmation at a company with several PM roles
    # must NOT guess — this is the fanout guard.
    ev = _event("Thank you for applying to the Product Manager role")
    candidates = [
        _posting("product manager, payments"),
        _posting("product manager, risk"),
        _posting("product manager, platform"),
    ]
    assert _best_posting_id(ev, candidates) is None


def test_no_link_when_no_candidate_role_matches() -> None:
    ev = _event("Your application for the Data Scientist position")
    candidates = [_posting("product manager, growth"), _posting("product owner, billing")]
    assert _best_posting_id(ev, candidates) is None


def test_no_candidates_returns_none() -> None:
    assert _best_posting_id(_event("anything"), []) is None


def test_single_candidate_links_on_role_evidence() -> None:
    # One candidate + the subject names the role → link it.
    ev = _event("Application received: Global Digital Product Manager")
    candidates = [_posting("global digital product manager")]
    assert _best_posting_id(ev, candidates) == candidates[0].id


def test_single_candidate_without_role_evidence_does_not_link() -> None:
    # One candidate but the email names a different/absent role → no false link.
    ev = _event("Thank you for applying to John Hancock")
    candidates = [_posting("global digital product manager")]
    assert _best_posting_id(ev, candidates) is None


# ── DB-gated: closed-posting candidate window (fix/outcome-match-closed-window) ─
#
# Outcome emails arrive weeks after applying, by which point the posting is
# usually closed. The matcher now considers postings closed within
# OUTCOME_LINK_CLOSED_WINDOW_DAYS before the email's received_at — anchored
# per-outcome. These lock the window behaviour and the no-regression guarantees.


def _company() -> TargetCompany:
    return TargetCompany(
        name=f"Co-{uuid.uuid4().hex[:8]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"h-{uuid.uuid4().hex[:6]}",
    )


def _db_posting(
    *,
    title: str,
    target_company_id: uuid.UUID,
    closed_at: datetime | None = None,
) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=title,
        raw_title=title.title(),
        jd_text="Own the roadmap end to end.",
        jd_text_hash=f"jdhash-{suffix}",
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
        closed_at=closed_at,
    )


def _db_outcome(
    *,
    subject: str,
    target_company_id: uuid.UUID,
    received_at: datetime,
    job_posting_id: uuid.UUID | None = None,
    snippet: str | None = None,
) -> OutcomeEvent:
    suffix = uuid.uuid4().hex[:12]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        from_address=f"recruiter-{suffix}@example.com",
        from_domain="example.com",
        subject=subject,
        received_at=received_at,
        outcome_type="rejection_post_screen",  # type: ignore[arg-type]
        classifier_version="v_test",
        classifier_confidence=0.9,
        raw_snippet=snippet,
        target_company_id=target_company_id,
        job_posting_id=job_posting_id,
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_links_closed_posting_within_window(db_session: Any) -> None:
    """A posting closed 30 days before the email (inside the 90-day window) is a
    valid candidate and links — the whole point of the fix."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    received = datetime.now(tz=UTC)
    posting = _db_posting(
        title="product manager, risk analytics",
        target_company_id=tc.id,
        closed_at=received - timedelta(days=30),
    )
    db_session.add(posting)
    event = _db_outcome(
        subject="Update on your application for the Risk Analytics Product Manager role",
        target_company_id=tc.id,
        received_at=received,
    )
    db_session.add(event)
    await db_session.commit()

    report = await link_outcomes_to_postings(db_session)

    assert report.linked == 1
    await db_session.refresh(event)
    assert event.job_posting_id == posting.id


@_NEEDS_DB
@pytest.mark.asyncio
async def test_does_not_link_closed_posting_beyond_window(db_session: Any) -> None:
    """A posting closed 120 days before the email (past the 90-day window) is NOT
    a candidate — it stays unlinked, counted as no_candidate."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    received = datetime.now(tz=UTC)
    posting = _db_posting(
        title="product manager, risk analytics",
        target_company_id=tc.id,
        closed_at=received - timedelta(days=OUTCOME_LINK_CLOSED_WINDOW_DAYS + 30),
    )
    db_session.add(posting)
    event = _db_outcome(
        subject="Update on your application for the Risk Analytics Product Manager role",
        target_company_id=tc.id,
        received_at=received,
    )
    db_session.add(event)
    await db_session.commit()

    report = await link_outcomes_to_postings(db_session)

    assert report.linked == 0
    assert report.no_candidate == 1
    await db_session.refresh(event)
    assert event.job_posting_id is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_already_linked_row_is_untouched_on_rerun(db_session: Any) -> None:
    """An outcome that already has a job_posting_id is excluded by the WHERE
    clause — a re-run never re-scores or rewrites it (idempotent; the fix can
    only ADD links, never change existing ones)."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    received = datetime.now(tz=UTC)
    linked_posting = _db_posting(title="product manager, payments", target_company_id=tc.id)
    # An open posting whose role the email actually names — it would be the match
    # if the row were ever re-scored. It must NOT be, because the row is linked.
    other_posting = _db_posting(title="product manager, risk analytics", target_company_id=tc.id)
    db_session.add_all([linked_posting, other_posting])
    await db_session.flush()

    event = _db_outcome(
        subject="Risk Analytics Product Manager — application update",
        target_company_id=tc.id,
        received_at=received,
        job_posting_id=linked_posting.id,  # already linked to a DIFFERENT posting
    )
    db_session.add(event)
    await db_session.commit()

    report = await link_outcomes_to_postings(db_session)

    assert report.scanned == 0  # the linked row was never selected
    await db_session.refresh(event)
    assert event.job_posting_id == linked_posting.id  # unchanged


@_NEEDS_DB
@pytest.mark.asyncio
async def test_closed_sibling_within_margin_still_blocks_the_link(db_session: Any) -> None:
    """The fix must not weaken the no-guess rule: a closed-but-in-window sibling
    that scores within _MARGIN of an open posting makes the match ambiguous, so
    nothing links (vs. linking the lone open posting under the old open-only
    rule)."""
    tc = _company()
    db_session.add(tc)
    await db_session.flush()

    received = datetime.now(tz=UTC)
    # Both titles reduce to the same significant tokens {risk, analytics}
    # ("senior"/"product"/"manager" are stopwords), so both score 1.0 against the
    # email — within the margin → ambiguous.
    open_posting = _db_posting(title="product manager, risk analytics", target_company_id=tc.id)
    closed_sibling = _db_posting(
        title="senior product manager, risk analytics",
        target_company_id=tc.id,
        closed_at=received - timedelta(days=20),  # in-window → now competes
    )
    db_session.add_all([open_posting, closed_sibling])
    event = _db_outcome(
        subject="Risk Analytics Product Manager — update on your application",
        target_company_id=tc.id,
        received_at=received,
    )
    db_session.add(event)
    await db_session.commit()

    report = await link_outcomes_to_postings(db_session)

    assert report.linked == 0
    assert report.ambiguous == 1
    await db_session.refresh(event)
    assert event.job_posting_id is None
