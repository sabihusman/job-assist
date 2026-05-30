"""DB-gated tests for the surface re-point (feat/surface-linked-outcomes).

Three load-bearing contracts pinned here:

  1. ``state=applied`` unions the manual posting_action='applied' signal
     with the Gmail-derived ``application_confirmation`` outcome at
     company level. Manual-applied OR company-confirmed both surface on
     the Applied page.

  2. ``state=rejected`` re-points from the always-NULL job_posting_id
     to ``target_company_id``. Postings at a company that has a
     rejection outcome surface on the explicit Rejected view.

  3. **Critical**: the default Triage view (``state=triage``) is
     **byte-identical to main**. Neither application_confirmation nor
     rejection outcomes at a company hide other open postings at that
     company from the default Best Fit queue. The MeridianLink
     scenario from the PR brief pins this directly: 4 open PM roles +
     1 application_confirmation + 1 rejection_post_screen at the same
     company → all 4 open roles remain in default Triage.

Tests are DB-gated (need TEST_DATABASE_URL); run on CI's postgres
service. The state predicates can only be exercised end-to-end against
real SQL — pure tests on ``build_view_parts`` would only confirm the
WHERE-clause count, not the runtime semantics.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import (
    JobPosting,
    OutcomeEvent,
    PostingAction,
    TargetCompany,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Fixture factories ────────────────────────────────────────────────────────


def _company(name: str | None = None) -> TargetCompany:
    return TargetCompany(
        name=name or f"TestCo-{uuid.uuid4().hex[:6]}",
        tier=1,
        ats="ashby",
        ats_handle=f"handle-{uuid.uuid4().hex[:6]}",
    )


def _posting(
    *,
    target_company_id: uuid.UUID | None,
    title: str = "Senior Product Manager",
    fit_score: int | None = 80,
) -> JobPosting:
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
        fit_score=fit_score,
    )


def _outcome(
    *,
    outcome_type: str,
    target_company_id: uuid.UUID | None,
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
    )


def _manual_applied(posting_id: uuid.UUID) -> PostingAction:
    """Record the operator's keyboard-``4`` action on a specific posting."""
    return PostingAction(
        job_posting_id=posting_id,
        action_type="applied",  # type: ignore[arg-type]
        created_at=datetime.now(tz=UTC),
    )


async def _list_postings(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    """Helper that asks ``GET /postings`` with sane defaults for tests:
    no per-company cap (so a company with 4 postings yields 4 rows), and
    a high limit so result-counting is unambiguous."""
    params.setdefault("per_company_cap", 0)
    params.setdefault("limit", 100)
    resp = await client.get("/postings", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


# ── (1) state=applied union semantics ────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_applied_includes_manual_action(db_session: Any) -> None:
    """The pre-PR ``posting_action.action_type='applied'`` signal still
    surfaces postings on the Applied view — no regression."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id, title=f"Mgr-{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    await db_session.flush()
    db_session.add(_manual_applied(p.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="applied")

    titles = {item["role"]["title"] for item in items}
    assert p.normalized_title in titles


@_NEEDS_DB
@pytest.mark.asyncio
async def test_applied_includes_company_confirmed_without_manual_action(
    db_session: Any,
) -> None:
    """A posting with NO manual action surfaces on Applied when there's
    an ``application_confirmation`` outcome at its target_company. The
    Gmail-derived signal is the new half of the union."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id, title=f"Mgr-{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.add(
        _outcome(
            outcome_type="application_confirmation",
            target_company_id=tc.id,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="applied")

    titles = {item["role"]["title"] for item in items}
    assert p.normalized_title in titles


@_NEEDS_DB
@pytest.mark.asyncio
async def test_applied_excludes_posting_with_no_signals(db_session: Any) -> None:
    """A clean posting (no manual action, no company-level confirmation)
    must NOT appear on the Applied view. Sanity that the union isn't
    over-matching."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id, title=f"Mgr-{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="applied")

    titles = {item["role"]["title"] for item in items}
    assert p.normalized_title not in titles


# ── (2) state=rejected re-point ──────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_surfaces_when_company_has_rejection_outcome(
    db_session: Any,
) -> None:
    """A posting at a company with a linked rejection_* outcome surfaces
    on the explicit Rejected view. The post-PR predicate joins via
    target_company_id (re-pointed from the always-NULL job_posting_id)."""
    from job_assist.main import app

    tc = _company()
    db_session.add(tc)
    await db_session.flush()
    p = _posting(target_company_id=tc.id, title=f"Mgr-{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.add(
        _outcome(
            outcome_type="rejection_post_screen",
            target_company_id=tc.id,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="rejected")

    titles = {item["role"]["title"] for item in items}
    assert p.normalized_title in titles


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_excludes_posting_at_company_with_no_rejection(
    db_session: Any,
) -> None:
    """Other companies' rejections must NOT bleed onto this posting."""
    from job_assist.main import app

    tc_clean = _company("CompanyClean")
    tc_rejected = _company("CompanyRejected")
    db_session.add_all([tc_clean, tc_rejected])
    await db_session.flush()
    p_clean = _posting(target_company_id=tc_clean.id, title=f"Clean-{uuid.uuid4().hex[:6]}")
    db_session.add(p_clean)
    # Rejection on a DIFFERENT company — must not surface p_clean.
    db_session.add(
        _outcome(
            outcome_type="rejection_post_screen",
            target_company_id=tc_rejected.id,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="rejected")

    titles = {item["role"]["title"] for item in items}
    assert p_clean.normalized_title not in titles


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rejected_safe_against_null_target_company_id(db_session: Any) -> None:
    """A posting with ``target_company_id IS NULL`` must never match a
    rejection outcome with NULL target — the defensive ``IS NOT NULL``
    guard in the predicate covers SQL's three-valued ``NULL = NULL``."""
    from job_assist.main import app

    p = _posting(target_company_id=None, title=f"Orphan-{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.add(_outcome(outcome_type="rejection_post_screen", target_company_id=None))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="rejected")

    titles = {item["role"]["title"] for item in items}
    assert p.normalized_title not in titles


# ── (3) THE LOAD-BEARING CONTRACT: default Triage unchanged ──────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_meridianlink_scenario_open_roles_remain_in_default_triage(
    db_session: Any,
) -> None:
    """The brief's exact MeridianLink scenario:

      * 1 ``application_confirmation`` linked at MeridianLink.
      * 1 ``rejection_post_screen`` linked at MeridianLink.
      * 4 open PM roles at MeridianLink with no posting_action rows.

    Expected: all 4 open roles surface in default Best Fit Triage
    (``state=triage``, the frontend default). Neither outcome blunt-hides
    them. The operator continues to triage these manually via the ``4``
    key, which would write a per-posting ``posting_action`` row and hide
    only the touched posting from triage.
    """
    from job_assist.main import app

    tc = _company("MeridianLink-Test")
    db_session.add(tc)
    await db_session.flush()

    titles = [f"PM-Role-{i}-{uuid.uuid4().hex[:6]}" for i in range(4)]
    postings = [
        _posting(target_company_id=tc.id, title=title, fit_score=80 + i)
        for i, title in enumerate(titles)
    ]
    db_session.add_all(postings)
    db_session.add(_outcome(outcome_type="application_confirmation", target_company_id=tc.id))
    db_session.add(_outcome(outcome_type="rejection_post_screen", target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="triage")

    surfaced_titles = {item["role"]["title"] for item in items}
    for title in titles:
        # Each open MeridianLink role must remain in default Triage —
        # this is THE assertion the PR was built around.
        assert title.lower() in surfaced_titles, (
            f"Posting {title!r} was hidden from default Triage. The PR's "
            "load-bearing contract is that company-linked outcomes do "
            "NOT modify the default Triage queue."
        )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_meridianlink_scenario_applied_view_includes_company_roles(
    db_session: Any,
) -> None:
    """Counterpart to the test above: the same 4 MeridianLink roles
    DO surface on the explicit Applied view because the company has a
    linked application_confirmation. The Gmail signal is visible where
    it's supposed to be (Applied page) and silent where it's supposed to
    be (default Triage)."""
    from job_assist.main import app

    tc = _company("MeridianLink-Test")
    db_session.add(tc)
    await db_session.flush()

    titles = [f"PM-Role-{i}-{uuid.uuid4().hex[:6]}" for i in range(4)]
    db_session.add_all(
        _posting(target_company_id=tc.id, title=t, fit_score=80 + i) for i, t in enumerate(titles)
    )
    db_session.add(_outcome(outcome_type="application_confirmation", target_company_id=tc.id))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        items = await _list_postings(client, state="applied")

    surfaced_titles = {item["role"]["title"] for item in items}
    for title in titles:
        assert title.lower() in surfaced_titles
