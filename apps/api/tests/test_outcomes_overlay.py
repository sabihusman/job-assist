"""DB-gated tests for the /outcomes manual-overlay fields (feat/applied-unified).

The unified Applied view fuses Gmail outcomes with the manual application_state.
To resolve "manual-vs-Gmail" server-side without a second round-trip, GET
/outcomes LEFT JOINs the posting an email was matched to (via #162's
``job_posting_id``) and exposes:

  * ``posting_title``  — the real role title (NULL when unlinked).
  * ``manual_status``  — the authoritative manual application_state on that
    posting (NULL when none).

Both are POSTING-SPECIFIC by construction: the join is on
``outcome_event.job_posting_id``, which #162 only ever sets to ONE specifically
matched posting. These tests pin that an unlinked sibling outcome at the same
company never inherits a manual status — the no-fanout guard (#157).

DB-gated (need TEST_DATABASE_URL); run on CI's postgres service.
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
    TargetCompany,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _company(name: str | None = None) -> TargetCompany:
    return TargetCompany(
        name=name or f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="ashby",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(*, target_company_id: uuid.UUID | None, title: str) -> JobPosting:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    return JobPosting(
        canonical_company_name="TestCo",
        target_company_id=target_company_id,
        normalized_title=title.lower(),
        raw_title=title,
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


def _outcome(
    *,
    outcome_type: str = "application_confirmation",
    target_company_id: uuid.UUID | None,
    job_posting_id: uuid.UUID | None = None,
) -> OutcomeEvent:
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
        job_posting_id=job_posting_id,
    )


async def _list_outcomes(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    resp = await client.get("/outcomes", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


@_NEEDS_DB
@pytest.mark.asyncio
async def test_linked_outcome_surfaces_posting_title_and_manual_status(db_session: Any) -> None:
    """A Gmail outcome matched to a posting that carries a manual status exposes
    BOTH the real role title and the authoritative manual status."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id, title=f"Product Manager Risk {uuid.uuid4().hex[:6]}")
    db_session.add(p)
    await db_session.flush()
    db_session.add(ApplicationState(job_posting_id=p.id, status="offer"))
    db_session.add(_outcome(target_company_id=tc.id, job_posting_id=p.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_outcomes(client, job_related=True)

    linked = [o for o in items if o["posting_id"] == str(p.id)]
    assert len(linked) == 1
    assert linked[0]["posting_title"] == p.normalized_title
    assert linked[0]["manual_status"] == "offer"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_unlinked_outcome_has_null_overlay(db_session: Any) -> None:
    """A direct-application outcome (job_posting_id NULL) has no posting_title
    and no manual_status — there is no posting to overlay."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    db_session.add(_outcome(target_company_id=tc.id, job_posting_id=None))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_outcomes(client, job_related=True)

    assert items, "expected the unlinked outcome to still surface"
    for o in items:
        if o["posting_id"] is None:
            assert o["posting_title"] is None
            assert o["manual_status"] is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_no_fanout_manual_status_does_not_bleed_to_sibling_outcome(db_session: Any) -> None:
    """THE no-fanout guard: a manual status on posting A must NOT appear on an
    outcome that is linked to a DIFFERENT posting B (or unlinked) at the same
    company. The join is on job_posting_id, so each outcome only ever reflects
    the manual status of the one posting it was specifically matched to."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p_offer = _posting(target_company_id=tc.id, title=f"PM-A-{uuid.uuid4().hex[:6]}")
    p_plain = _posting(target_company_id=tc.id, title=f"PM-B-{uuid.uuid4().hex[:6]}")
    db_session.add_all([p_offer, p_plain])
    await db_session.flush()
    # Manual "offer" lives ONLY on p_offer.
    db_session.add(ApplicationState(job_posting_id=p_offer.id, status="offer"))
    # One outcome linked to p_offer, one to the sibling p_plain.
    db_session.add(_outcome(target_company_id=tc.id, job_posting_id=p_offer.id))
    db_session.add(_outcome(target_company_id=tc.id, job_posting_id=p_plain.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_outcomes(client, job_related=True)

    by_posting = {o["posting_id"]: o for o in items if o["posting_id"]}
    assert by_posting[str(p_offer.id)]["manual_status"] == "offer"
    # The sibling outcome must NOT inherit the offer — this is the guard.
    assert by_posting[str(p_plain.id)]["manual_status"] is None
