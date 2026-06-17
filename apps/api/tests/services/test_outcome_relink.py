"""Tests for services/outcome_relink.py (feat/outcome-company-linking).

Pure tests on _raw_email_from_outcome (no DB), DB-gated tests on
``relink_unmatched`` covering domain-only and classifier paths plus the
unlinked-WHERE-clause idempotency guarantee.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from job_assist.db.models import OutcomeEvent, TargetCompany
from job_assist.gmail.models import ClassificationResult, RawEmail
from job_assist.services.outcome_relink import (
    _raw_email_from_outcome,
    relink_unmatched,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure tests ───────────────────────────────────────────────────────────────


def _outcome(
    *,
    outcome_type: str,
    from_domain: str = "example.com",
    from_address: str | None = None,
    raw_snippet: str | None = "snippet body",
    target_company_id: uuid.UUID | None = None,
    received_at: datetime | None = None,
) -> OutcomeEvent:
    suffix = uuid.uuid4().hex[:12]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        from_address=from_address or f"hr-{suffix}@{from_domain}",
        from_domain=from_domain,
        subject=f"Subject {suffix}",
        received_at=received_at or datetime.now(tz=UTC),
        outcome_type=outcome_type,  # type: ignore[arg-type]
        classifier_version="v_test",
        classifier_confidence=0.9,
        raw_snippet=raw_snippet,
        target_company_id=target_company_id,
    )


def test_raw_email_from_outcome_carries_persisted_fields() -> None:
    """Pure — proves the classifier-input adapter passes through every
    column the prompt template references."""
    event = _outcome(
        outcome_type="application_confirmation",
        from_address="hr@meridianlink.com",
        from_domain="meridianlink.com",
        raw_snippet="Thanks for applying to MeridianLink!",
    )
    raw = _raw_email_from_outcome(event)
    assert raw.message_id == event.email_message_id
    assert raw.from_address == "hr@meridianlink.com"
    assert raw.from_domain == "meridianlink.com"
    assert raw.subject == event.subject
    assert raw.snippet == "Thanks for applying to MeridianLink!"
    # Body fields the original classifier was called with are not
    # persisted on outcome_event; the adapter fills them with empty
    # strings so the prompt template still renders.
    assert raw.body_text == ""
    assert raw.body_html == ""


def test_raw_email_from_outcome_tolerates_null_snippet() -> None:
    """raw_snippet is nullable on the model — adapter must produce ''."""
    event = _outcome(outcome_type="rejection_post_screen", raw_snippet=None)
    raw = _raw_email_from_outcome(event)
    assert raw.snippet == ""


# ── DB-gated tests ───────────────────────────────────────────────────────────


def _company(name: str, *, domain: str | None = None) -> TargetCompany:
    return TargetCompany(
        name=name,
        tier=1,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
        domain=domain,
    )


class _StaticClassifier:
    """Test double — returns a preset extracted_company for every call.

    The relink service discards outcome_type from re-classification (the
    docstring is explicit) so the stub returns any valid value.
    """

    def __init__(self, extracted: str | None) -> None:
        self.extracted = extracted
        self.call_count = 0

    async def classify(self, email: RawEmail) -> ClassificationResult:
        self.call_count += 1
        return ClassificationResult(
            outcome_type="application_confirmation",
            confidence=0.9,
            extracted_company=self.extracted,
        )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_relink_domain_only_matches_when_domain_seeded(
    db_session: Any,
) -> None:
    """Domain path fires when target_company.domain matches the persisted
    from_domain and the row was previously unlinked."""
    tc = _company("MeridianLink", domain="meridianlink.com")
    db_session.add(tc)
    await db_session.flush()

    db_session.add(
        _outcome(
            outcome_type="application_confirmation",
            from_domain="meridianlink.com",
        )
    )
    await db_session.commit()

    report = await relink_unmatched(db_session, classifier=None, use_classifier=False)
    assert report.scanned == 1
    assert report.domain_matched == 1
    assert report.fuzzy_matched == 0
    assert report.unmatched == 0

    # Row in DB now points at the company.
    linked = (
        (
            await db_session.execute(
                select(OutcomeEvent).where(OutcomeEvent.target_company_id == tc.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(linked) == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_relink_skips_already_linked_rows(db_session: Any) -> None:
    """Idempotency guarantee — re-running never overwrites an existing link."""
    tc_a = _company("CompanyA", domain="companya.com")
    tc_b = _company("CompanyB", domain="companya.com")  # same domain, ambiguous
    db_session.add_all([tc_a, tc_b])
    await db_session.flush()

    # Pre-link to a specific company — relink must NOT touch this row.
    db_session.add(
        _outcome(
            outcome_type="rejection_post_screen",
            from_domain="companya.com",
            target_company_id=tc_a.id,
        )
    )
    await db_session.commit()

    report = await relink_unmatched(db_session, classifier=None, use_classifier=False)
    assert report.scanned == 0
    assert report.domain_matched == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_relink_skips_unrelated_and_unclassified(db_session: Any) -> None:
    """Job-relatedness gate — never spend a Gemini call on noise."""
    tc = _company("MeridianLink", domain="meridianlink.com")
    db_session.add(tc)
    await db_session.flush()

    # Both rows would match the domain, but neither is job-related.
    db_session.add_all(
        [
            _outcome(outcome_type="unrelated", from_domain="meridianlink.com"),
            _outcome(outcome_type="unclassified", from_domain="meridianlink.com"),
        ]
    )
    await db_session.commit()

    report = await relink_unmatched(db_session, classifier=None, use_classifier=False)
    assert report.scanned == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_relink_classifier_path_picks_up_fuzzy_match(db_session: Any) -> None:
    """When domain path fails AND use_classifier=True, the classifier's
    extracted_company drives a fuzzy name match against target_company.name."""
    tc = _company("MeridianLink", domain=None)  # NO domain — domain path will miss
    db_session.add(tc)
    await db_session.flush()

    # from_domain is an ATS sender (not the company's domain) — typical
    # production shape.
    db_session.add(
        _outcome(
            outcome_type="application_confirmation",
            from_domain="ashbyhq.com",
            raw_snippet="Thanks for applying to MeridianLink Recruiting Team",
        )
    )
    await db_session.commit()

    classifier = _StaticClassifier(extracted="the MeridianLink Recruiting Team")
    report = await relink_unmatched(db_session, classifier, use_classifier=True)

    assert report.scanned == 1
    assert report.domain_matched == 0
    assert report.fuzzy_matched == 1
    assert report.unmatched == 0
    assert classifier.call_count == 1


@_NEEDS_DB
@pytest.mark.asyncio
async def test_relink_classifier_error_does_not_abort_sweep(db_session: Any) -> None:
    """A classifier failure on one row is logged + counted; the sweep
    proceeds to subsequent rows so a single bad email doesn't stall the
    whole backfill."""

    class _FlakyClassifier:
        def __init__(self) -> None:
            self.calls = 0

        async def classify(self, email: RawEmail) -> ClassificationResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated Gemini failure")
            return ClassificationResult(
                outcome_type="application_confirmation",
                confidence=0.9,
                extracted_company="MeridianLink",
            )

    tc = _company("MeridianLink", domain=None)
    db_session.add(tc)
    await db_session.flush()
    now = datetime.now(tz=UTC)
    db_session.add_all(
        [
            _outcome(
                outcome_type="application_confirmation",
                from_domain="ashbyhq.com",
                received_at=now,
            ),
            _outcome(
                outcome_type="application_confirmation",
                from_domain="greenhouse.io",
                received_at=now,
            ),
        ]
    )
    await db_session.commit()

    classifier = _FlakyClassifier()
    report = await relink_unmatched(db_session, classifier, use_classifier=True)

    assert report.scanned == 2
    assert report.classifier_errors == 1
    assert report.fuzzy_matched == 1
    assert report.unmatched == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_relink_use_classifier_requires_classifier_argument() -> None:
    """Defensive guard — calling with use_classifier=True but no
    classifier raises rather than silently no-op'ing."""
    with pytest.raises(ValueError, match="classifier is required"):
        await relink_unmatched(None, None, use_classifier=True)  # type: ignore[arg-type]


# ── Non-object JSON through the FULL classifier→relink path ───────────────────
# Regression coverage: a REAL EmailClassifier whose model returns syntactically
# valid but non-object JSON (a top-level array, or a bare string) must not raise
# through relink — the row is left UNLINKED and the sweep continues. classify()
# degrades non-object JSON to unclassified/no-company (gmail/classifier.py, PR
# #198 fix(audit)), so relink sees extracted_company=None → unmatched (NOT a
# classifier_error, which is reserved for an actual raised exception). This
# exercises the end-to-end path the isolated classifier tests + the
# raising-stub relink test don't cover together.


@contextmanager
def _real_classify_returning(raw_json: str) -> Iterator[Any]:
    """Yield a real EmailClassifier whose model output is ``raw_json``.

    We bypass ``__init__`` (which imports the google-genai SDK) and patch the
    network call ``_call_model`` to replay ``raw_json`` verbatim, so the GENUINE
    ``classify()`` JSON-parsing / non-object-handling path runs — no SDK, no
    fragile sys.modules shim that the full-suite import order can defeat.
    """
    from job_assist.gmail.classifier import EmailClassifier

    clf = object.__new__(EmailClassifier)
    clf._model = "test-model"  # type: ignore[attr-defined]
    clf._last_request_ts = 0.0  # type: ignore[attr-defined]
    clf._lock = asyncio.Lock()  # type: ignore[attr-defined]

    async def _fake_call_model(prompt: str) -> str:
        return raw_json

    clf._call_model = _fake_call_model  # type: ignore[attr-defined,method-assign]
    with patch("job_assist.gmail.classifier._MIN_REQUEST_GAP_S", 0.0):
        yield clf


@_NEEDS_DB
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_json",
    [
        '["MeridianLink", "another"]',  # top-level array of non-objects
        '"just a bare string"',  # bare JSON string
    ],
)
async def test_relink_non_object_json_leaves_row_unlinked(db_session: Any, raw_json: str) -> None:
    """Gemini returning non-object JSON must NOT crash relink: the real
    classify() degrades it to unclassified/no-company, so the row stays
    unlinked, the run completes, and it counts as unmatched (NOT a
    classifier_error — no exception was raised)."""
    tc = _company("MeridianLink", domain=None)  # domain path misses
    db_session.add(tc)
    await db_session.flush()
    db_session.add(
        _outcome(
            outcome_type="application_confirmation",
            from_domain="ashbyhq.com",  # ATS sender — not the company domain
            raw_snippet="Thanks for applying to MeridianLink",
        )
    )
    await db_session.commit()

    with _real_classify_returning(raw_json) as classifier:
        # Must not raise.
        report = await relink_unmatched(db_session, classifier, use_classifier=True)

    assert report.scanned == 1
    assert report.classifier_errors == 0  # graceful degrade, not an exception
    assert report.domain_matched == 0
    assert report.fuzzy_matched == 0
    assert report.unmatched == 1

    # The row is left UNLINKED, never crashed.
    still_unlinked = (
        (
            await db_session.execute(
                select(OutcomeEvent).where(OutcomeEvent.target_company_id.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert len(still_unlinked) == 1
