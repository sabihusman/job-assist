"""Tests for GET /admin/outcomes/stats (feat/admin-outcomes-stats).

Two DB-gated tests cover the two response modes:

  1. No ``target_company_id`` — corpus-wide ``OutcomesOverallStats``:
     totals, company-link fill rate broken down by ``outcome_type``,
     posting-link fill (expected 0 — deferred by design).
  2. With ``target_company_id`` — ``OutcomesForCompanyStats``: per-
     outcome_type count for that one company, filtered server-side.

All counts come from SQL ``COUNT(... GROUP BY ...)`` aggregates — the
endpoint never pulls the underlying ``outcome_event`` rows.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import ApplicationResume, JobPosting, OutcomeEvent, TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _company(name: str | None = None) -> TargetCompany:
    return TargetCompany(
        name=name or f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="greenhouse",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _outcome(
    *,
    outcome_type: str,
    target_company_id: uuid.UUID | None = None,
    job_posting_id: uuid.UUID | None = None,
) -> OutcomeEvent:
    """Build an OutcomeEvent with all NOT NULL columns populated."""
    suffix = uuid.uuid4().hex[:12]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        from_address=f"recruiter-{suffix}@example.com",
        from_domain="example.com",
        subject=f"Re: your application ({suffix})",
        received_at=datetime.now(tz=UTC),
        outcome_type=outcome_type,  # type: ignore[arg-type]
        classifier_version="v_test",
        classifier_confidence=0.9,
        target_company_id=target_company_id,
        job_posting_id=job_posting_id,
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_overall_stats_reports_company_fill_per_outcome_type(
    db_session: Any,
) -> None:
    """Corpus-wide call: counts per outcome_type split by linked / unlinked,
    and the deferred posting-link diagnostic comes back as 0."""
    from job_assist.main import app

    tc_a = _company("CompanyA")
    tc_b = _company("CompanyB")
    db_session.add_all([tc_a, tc_b])
    await db_session.flush()

    # application_confirmation: 2 linked to companies, 1 unlinked.
    # rejection_post_screen:     1 linked, 2 unlinked.
    # unrelated:                 0 linked, 3 unlinked (the dominant
    #                            shape in production).
    db_session.add_all(
        [
            _outcome(outcome_type="application_confirmation", target_company_id=tc_a.id),
            _outcome(outcome_type="application_confirmation", target_company_id=tc_b.id),
            _outcome(outcome_type="application_confirmation"),
            _outcome(outcome_type="rejection_post_screen", target_company_id=tc_a.id),
            _outcome(outcome_type="rejection_post_screen"),
            _outcome(outcome_type="rejection_post_screen"),
            _outcome(outcome_type="unrelated"),
            _outcome(outcome_type="unrelated"),
            _outcome(outcome_type="unrelated"),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/outcomes/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_rows"] == 9
    assert data["total_linked_to_company"] == 3
    # Deferred-by-design — backfill.py never sets job_posting_id today.
    assert data["total_linked_to_posting"] == 0

    by_type = {row["outcome_type"]: row for row in data["by_outcome_type"]}
    assert by_type["application_confirmation"]["linked_to_company"] == 2
    assert by_type["application_confirmation"]["unlinked"] == 1
    assert by_type["rejection_post_screen"]["linked_to_company"] == 1
    assert by_type["rejection_post_screen"]["unlinked"] == 2
    assert by_type["unrelated"]["linked_to_company"] == 0
    assert by_type["unrelated"]["unlinked"] == 3


@_NEEDS_DB
@pytest.mark.asyncio
async def test_outcome_linking_diagnostic_runs_all_four_queries(db_session: Any) -> None:
    """The feedback-loop coverage diagnostic executes its four fixed SELECTs and
    returns each result set. Verifies the SQL is valid (right tables/columns) and
    the posting-link counts/pct compute correctly."""
    from job_assist.main import app

    # 3 outcomes, none linked to a posting (job_posting_id NULL) — the dominant
    # production shape today (Gmail backfill defers posting-linking).
    db_session.add_all(
        [
            _outcome(outcome_type="application_confirmation"),
            _outcome(outcome_type="rejection_post_screen"),
            _outcome(outcome_type="rejection_post_screen"),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/diagnostics/outcome-linking")

    assert resp.status_code == 200, resp.text
    d = resp.json()

    # q1: 3 total, 0 linked to a posting, 0.0% — the ratio survives JSON as float.
    assert d["q1_overall"]["total_outcomes"] == 3
    assert d["q1_overall"]["linked_to_posting"] == 0
    assert d["q1_overall"]["pct_linked"] == 0.0

    # q2: per-type split, ordered by total DESC.
    by_type = {r["outcome_type"]: r for r in d["q2_by_outcome_type"]}
    assert by_type["rejection_post_screen"]["total"] == 2
    assert by_type["rejection_post_screen"]["linked"] == 0

    # q3 / q4: run without fixtures → 0 / shape only (no resume or application rows).
    assert d["q3_complete_triples"] == 0
    assert set(d["q4_resume_coverage"].keys()) == {"total_resumes", "with_resume_text"}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_company_filtered_stats_returns_per_outcome_counts(
    db_session: Any,
) -> None:
    """With target_company_id: only that company's rows, grouped by
    outcome_type. Other companies' rows do not leak in."""
    from job_assist.main import app

    tc_target = _company("Target")
    tc_other = _company("Other")
    db_session.add_all([tc_target, tc_other])
    await db_session.flush()

    db_session.add_all(
        [
            # Target company: 2 confirmations + 1 rejection.
            _outcome(outcome_type="application_confirmation", target_company_id=tc_target.id),
            _outcome(outcome_type="application_confirmation", target_company_id=tc_target.id),
            _outcome(outcome_type="rejection_post_screen", target_company_id=tc_target.id),
            # Other company — must NOT appear in the response.
            _outcome(outcome_type="rejection_pre_screen", target_company_id=tc_other.id),
            # Unlinked rejection — must NOT appear either.
            _outcome(outcome_type="rejection_pre_screen"),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/admin/outcomes/stats?target_company_id={tc_target.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["target_company_id"] == str(tc_target.id)
    assert data["total_rows"] == 3
    by_type = {row["outcome_type"]: row["count"] for row in data["by_outcome_type"]}
    assert by_type == {
        "application_confirmation": 2,
        "rejection_post_screen": 1,
    }


@_NEEDS_DB
@pytest.mark.asyncio
async def test_company_filtered_stats_unknown_company_returns_zero(
    db_session: Any,
) -> None:
    """Random UUID with no outcome_event rows → 200 with empty breakdown,
    not a 404. The endpoint reports state, doesn't validate existence."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/admin/outcomes/stats?target_company_id={uuid.uuid4()}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_rows"] == 0
    assert data["by_outcome_type"] == []


@_NEEDS_DB
@pytest.mark.asyncio
async def test_no_candidate_breakdown_runs_and_classifies_zero_posting_company(
    db_session: Any,
) -> None:
    """The no_candidate breakdown executes all three queries and correctly counts
    a company-resolved, unlinked, linkable outcome whose company has no postings
    into the zero-postings bucket."""
    from job_assist.main import app

    # Company with NO job_posting rows + one unlinked, company-resolved, linkable
    # outcome — exactly the "company we never crawled" shape the query isolates.
    tc = _company("NoPostingsCo")
    db_session.add(tc)
    await db_session.flush()
    db_session.add(_outcome(outcome_type="rejection_post_screen", target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/diagnostics/no-candidate-breakdown")

    assert resp.status_code == 200, resp.text
    d = resp.json()

    q1 = d["q1_company_posting_coverage"]
    assert q1["scanned"] >= 1
    assert q1["company_zero_postings"] >= 1  # NoPostingsCo's outcome lands here

    # q2 runs (companies-with-postings set) and exposes its buckets; this company
    # contributes nothing to it (no postings) but the shape must be present.
    q2 = d["q2_recency_for_companies_with_postings"]
    assert set(q2.keys()) == {
        "with_postings",
        "has_open_posting",
        "closed_le_90d",
        "closed_90_180d",
        "closed_gt_180d",
    }

    # q3 source breakdown ran and is a list of {source, companies, outcomes}.
    assert isinstance(d["q3_by_company_source"], list)
    assert all({"source", "companies", "outcomes"} <= set(r) for r in d["q3_by_company_source"])


@_NEEDS_DB
@pytest.mark.asyncio
async def test_resume_storage_diagnostic_runs_all_queries(db_session: Any) -> None:
    """The resume-storage diagnostic executes its count query + both listings and
    returns the right shape — proves the SQL (table/column names) is valid."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/diagnostics/resume-storage")

    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert set(d["counts"].keys()) == {
        "resume_version",
        "application_resume",
        "application_state",
        "posting_action_applied",
    }
    assert all(isinstance(v, int) for v in d["counts"].values())
    assert isinstance(d["resume_version_rows"], list)
    assert isinstance(d["application_resume_rows"], list)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rag_corpus_diagnostic_runs_all_queries(db_session: Any) -> None:
    """The RAG-corpus diagnostic executes its four queries and returns the right
    shape (proves the SQL/table/column names are valid)."""
    from job_assist.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/diagnostics/rag-corpus")

    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert set(d["q1_table_baselines"].keys()) == {
        "resume_version",
        "application_resume",
        "application_state",
        "posting_action_applied",
    }
    assert isinstance(d["q2_apply_plus_resume"], int)
    assert set(d["q3_complete_triples"].keys()) == {"total", "by_outcome_type"}
    assert isinstance(d["q3_complete_triples"]["by_outcome_type"], list)
    assert set(d["q4_resume_text_availability"].keys()) == {
        "application_resume_total",
        "with_resume_text",
        "with_file_blob",
        "blob_only_no_text",
    }


@_NEEDS_DB
@pytest.mark.asyncio
async def test_q4_counts_application_resume_directly_not_application_state(
    db_session: Any,
) -> None:
    """q4 anchor fix: resume coverage counts application_resume DIRECTLY, so it
    reflects the true resume count even with ZERO application_state rows (the
    apply button writes posting_action, not application_state)."""
    from datetime import UTC, datetime

    from job_assist.main import app

    now = datetime.now(tz=UTC)

    def _posting(suffix: str) -> JobPosting:
        return JobPosting(
            canonical_company_name="Co",
            normalized_title="pm",
            raw_title="PM",
            remote_type="remote",
            role_family="product_management",
            seniority_level="pm",
            jd_text="x",
            jd_text_hash="0" * 64,
            content_hash=f"h-{suffix}",
            first_seen_at=now,
            last_seen_at=now,
        )

    postings = [_posting("a"), _posting("b"), _posting("c")]
    db_session.add_all(postings)
    await db_session.flush()
    # 3 resumes; only 1 carries non-empty resume_text. NO application_state rows.
    db_session.add_all(
        [
            ApplicationResume(
                job_posting_id=postings[0].id, file_name="a.docx", resume_text="full"
            ),
            ApplicationResume(job_posting_id=postings[1].id, file_name="b.docx", resume_text=""),
            ApplicationResume(job_posting_id=postings[2].id, file_name="c.docx"),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/diagnostics/outcome-linking")
    assert resp.status_code == 200, resp.text
    q4 = resp.json()["q4_resume_coverage"]
    assert q4["total_resumes"] == 3  # counts application_resume, not application_state (0)
    assert q4["with_resume_text"] == 1
