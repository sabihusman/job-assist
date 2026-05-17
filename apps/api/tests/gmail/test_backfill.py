"""Integration tests for the Gmail backfill orchestrator.

Exercises ``run_backfill`` end-to-end against a real Postgres (gated on
``TEST_DATABASE_URL``), with both the Gmail client and the classifier
mocked. Covers:

  * Inserts one ``outcome_event`` per classified message.
  * Idempotency: re-running over the same message IDs adds zero rows.
  * Pre-filter: ``OBVIOUS_NON_JOB_DOMAINS`` senders skip the classifier.
  * ``target_company`` linking via (a) ``domain`` exact match and
    (b) extracted-name normalisation.
  * Fetch / classifier errors are counted, not raised.

All fixture emails are synthetic.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from job_assist.db.models import OutcomeEvent, TargetCompany
from job_assist.gmail.models import ClassificationResult, RawEmail

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Mocks ──────────────────────────────────────────────────────────────────────


class _FakeGmail:
    """Stand-in for GmailClient. Holds a list of RawEmails to serve.

    Records every Gmail search query passed to ``list_message_ids`` so
    poll tests can assert the watermark turned into the right ``after:``
    parameter.
    """

    def __init__(
        self,
        emails: list[RawEmail],
        *,
        fail_ids: set[str] | None = None,
    ) -> None:
        self._by_id = {e.message_id: e for e in emails}
        self._order = [e.message_id for e in emails]
        self._fail_ids = fail_ids or set()
        self.get_calls: list[str] = []
        self.list_queries: list[str] = []

    async def list_message_ids(
        self,
        query: str,
        max_results_per_page: int = 500,
    ) -> list[str]:
        self.list_queries.append(query)
        return list(self._order)

    async def get_message(self, message_id: str) -> RawEmail:
        self.get_calls.append(message_id)
        if message_id in self._fail_ids:
            raise RuntimeError(f"simulated fetch failure for {message_id}")
        return self._by_id[message_id]


class _FakeClassifier:
    """Returns pre-canned classifications keyed by message_id."""

    def __init__(
        self,
        results: dict[str, ClassificationResult],
        *,
        fail_ids: set[str] | None = None,
        default: ClassificationResult | None = None,
    ) -> None:
        self._results = results
        self._fail_ids = fail_ids or set()
        self._default = default or ClassificationResult(outcome_type="unclassified", confidence=0.0)
        self.classify_calls: list[str] = []

    async def classify(self, email: RawEmail) -> ClassificationResult:
        self.classify_calls.append(email.message_id)
        if email.message_id in self._fail_ids:
            raise RuntimeError(f"simulated classify failure for {email.message_id}")
        return self._results.get(email.message_id, self._default)


def _email(
    message_id: str,
    *,
    from_address: str = "recruiter@example-company.com",
    subject: str = "Re: Your application",
    body: str = "Thanks for applying to ExampleCo.",
) -> RawEmail:
    return RawEmail(
        message_id=message_id,
        thread_id=f"thread_{message_id}",
        from_address=from_address,
        from_domain=from_address.partition("@")[2].lower(),
        subject=subject,
        received_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        body_text=body,
        snippet=body[:200],
    )


def _verdict(
    outcome: str, *, company: str | None = None, conf: float = 0.9
) -> ClassificationResult:
    return ClassificationResult(
        outcome_type=outcome,
        confidence=conf,
        extracted_company=company,
        reasoning="synthetic",
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_inserts_one_outcome_event_per_classified_message(db_session: Any) -> None:
    from job_assist.gmail.backfill import run_backfill

    emails = [
        _email("msg_a", from_address="recruiter@example-company.com"),
        _email("msg_b", from_address="auto-reject@anothercompany.com"),
    ]
    gmail = _FakeGmail(emails)
    classifier = _FakeClassifier(
        {
            "msg_a": _verdict("application_confirmation", company="ExampleCo"),
            "msg_b": _verdict("rejection_pre_screen", company="AnotherCo"),
        }
    )

    report = await run_backfill(db_session, gmail, classifier, days_back=60)
    assert report.fetched == 2
    assert report.classified_job_related == 2
    assert report.outcome_events_inserted == 2

    rows = (
        (await db_session.execute(select(OutcomeEvent).order_by(OutcomeEvent.email_message_id)))
        .scalars()
        .all()
    )
    assert [r.email_message_id for r in rows] == ["msg_a", "msg_b"]
    assert rows[0].outcome_type == "application_confirmation"
    assert rows[1].outcome_type == "rejection_pre_screen"
    assert rows[0].classifier_version.startswith("gemini-flash-lite")


@_NEEDS_DB
async def test_idempotent_second_run_inserts_zero(db_session: Any) -> None:
    from job_assist.gmail.backfill import run_backfill

    emails = [_email("msg_dup")]
    gmail = _FakeGmail(emails)
    classifier = _FakeClassifier({"msg_dup": _verdict("rejection_pre_screen")})

    await run_backfill(db_session, gmail, classifier, days_back=30)
    # Second run with a fresh classifier — its classify() must NOT be called
    # because the message ID is already in outcome_event.
    classifier_2 = _FakeClassifier({"msg_dup": _verdict("offer")})
    report = await run_backfill(db_session, _FakeGmail(emails), classifier_2, days_back=30)

    assert report.skipped_already_classified == 1
    assert report.outcome_events_inserted == 0
    assert classifier_2.classify_calls == []  # never asked for a verdict

    total = (await db_session.execute(select(func.count()).select_from(OutcomeEvent))).scalar_one()
    assert total == 1


@_NEEDS_DB
async def test_prefilter_skips_non_job_domains(db_session: Any) -> None:
    from job_assist.gmail.backfill import run_backfill

    emails = [
        _email("msg_gh", from_address="notifications@github.com"),
        _email("msg_real", from_address="hr@somecompany.com"),
    ]
    gmail = _FakeGmail(emails)
    classifier = _FakeClassifier({"msg_real": _verdict("rejection_pre_screen")})

    report = await run_backfill(db_session, gmail, classifier, days_back=10)
    assert report.skipped_prefilter == 1
    assert "msg_gh" not in classifier.classify_calls
    assert "msg_real" in classifier.classify_calls

    rows = (await db_session.execute(select(OutcomeEvent))).scalars().all()
    assert {r.email_message_id for r in rows} == {"msg_real"}


@_NEEDS_DB
async def test_target_company_linked_by_domain(db_session: Any) -> None:
    """When ``target_company.domain`` matches ``from_domain`` exactly, link the row."""
    from job_assist.gmail.backfill import run_backfill

    tc = TargetCompany(name="ExampleCo", tier=1, ats="greenhouse", domain="example-company.com")
    db_session.add(tc)
    await db_session.flush()

    emails = [_email("msg_dom", from_address="recruiter@example-company.com")]
    gmail = _FakeGmail(emails)
    classifier = _FakeClassifier({"msg_dom": _verdict("application_confirmation")})

    report = await run_backfill(db_session, gmail, classifier, days_back=30)
    assert report.target_company_links == 1

    row = (
        await db_session.execute(
            select(OutcomeEvent).where(OutcomeEvent.email_message_id == "msg_dom")
        )
    ).scalar_one()
    assert row.target_company_id == tc.id


@_NEEDS_DB
async def test_target_company_linked_by_extracted_name(db_session: Any) -> None:
    """Domain doesn't match, but the LLM-extracted name normalises to a seed row."""
    from job_assist.gmail.backfill import run_backfill

    tc = TargetCompany(name="ExampleCo Inc.", tier=2, ats="unknown")
    db_session.add(tc)
    await db_session.flush()

    emails = [_email("msg_name", from_address="info@unrelated-mail-relay.io")]
    gmail = _FakeGmail(emails)
    # Note: extracted_company is "exampleco" without suffix — normaliser
    # should match it to "ExampleCo Inc." in the seed.
    classifier = _FakeClassifier(
        {"msg_name": _verdict("rejection_pre_screen", company="exampleco")}
    )

    report = await run_backfill(db_session, gmail, classifier, days_back=30)
    assert report.target_company_links == 1

    row = (
        await db_session.execute(
            select(OutcomeEvent).where(OutcomeEvent.email_message_id == "msg_name")
        )
    ).scalar_one()
    assert row.target_company_id == tc.id


@_NEEDS_DB
async def test_unrelated_outcome_is_recorded_without_target_link(db_session: Any) -> None:
    from job_assist.gmail.backfill import run_backfill

    tc = TargetCompany(name="ExampleCo", tier=1, ats="greenhouse", domain="example-company.com")
    db_session.add(tc)
    await db_session.flush()

    emails = [_email("msg_unrelated", from_address="newsletter@example-company.com")]
    gmail = _FakeGmail(emails)
    classifier = _FakeClassifier({"msg_unrelated": _verdict("unrelated", company="ExampleCo")})

    report = await run_backfill(db_session, gmail, classifier, days_back=10)
    assert report.classified_unrelated == 1
    assert report.target_company_links == 0  # we skip linking for unrelated

    row = (
        await db_session.execute(
            select(OutcomeEvent).where(OutcomeEvent.email_message_id == "msg_unrelated")
        )
    ).scalar_one()
    assert row.outcome_type == "unrelated"
    assert row.target_company_id is None


@_NEEDS_DB
async def test_fetch_and_classifier_errors_are_counted_not_raised(db_session: Any) -> None:
    from job_assist.gmail.backfill import run_backfill

    emails = [
        _email("msg_ok"),
        _email("msg_fetch_fail"),
        _email("msg_classify_fail"),
    ]
    gmail = _FakeGmail(emails, fail_ids={"msg_fetch_fail"})
    classifier = _FakeClassifier(
        {"msg_ok": _verdict("offer")},
        fail_ids={"msg_classify_fail"},
    )

    report = await run_backfill(db_session, gmail, classifier, days_back=10)
    assert report.fetch_errors == 1
    assert report.classifier_errors == 1
    assert report.outcome_events_inserted == 1
