"""Pure tests for the Gmail-outcome → posting matcher (cross-link).

The matching logic is deterministic token overlap — no DB needed. These lock
the no-fanout guarantees: a confident single role match links; a generic /
ambiguous email at a multi-posting company links nothing.
"""

from __future__ import annotations

import uuid

from job_assist.db.models import JobPosting, OutcomeEvent
from job_assist.services.outcome_posting_match import (
    _best_posting_id,
    role_match_score,
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
