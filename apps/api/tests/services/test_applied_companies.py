"""Tests for services/applied_companies.py (feat/applied-company-tracking).

Pure tests on the subject extractor; DB-gated tests on the sweep that prove —
through the real endpoint paths — that tracking rows are created/linked/
suggested correctly and are NEVER picked up by the ingest cron driver.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from job_assist.db.models import OutcomeEvent, TargetCompany
from job_assist.main import get_ingest_plan, list_companies
from job_assist.services.applied_companies import company_from_subject, sync_applied_companies
from job_assist.services.broad_ingest import _ensure_shell_company

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure: subject → company extractor ─────────────────────────────────────────


def test_company_from_subject_extracts_common_patterns() -> None:
    assert company_from_subject("Thank you for applying to Solv Health") == "Solv Health"
    assert company_from_subject("Thank You for Applying to Goldman Sachs") == "Goldman Sachs"
    assert company_from_subject("Thank you for applying at Uphold!") == "Uphold"
    assert company_from_subject("Applying to Ramp - Senior PM") == "Ramp"


def test_company_from_subject_returns_none_for_generic() -> None:
    assert company_from_subject("Update on Your Application") is None
    assert company_from_subject("Application Received") is None
    assert company_from_subject("") is None
    assert company_from_subject(None) is None


# ── DB helpers ────────────────────────────────────────────────────────────────


def _confirmation(subject: str, *, received_at: datetime | None = None) -> OutcomeEvent:
    suffix = uuid.uuid4().hex[:12]
    return OutcomeEvent(
        email_message_id=f"msg-{suffix}",
        from_address=f"no-reply-{suffix}@greenhouse.io",
        from_domain="greenhouse.io",  # ATS vendor — must NEVER become a name
        subject=subject,
        received_at=received_at or datetime.now(tz=UTC),
        outcome_type="application_confirmation",  # type: ignore[arg-type]
        classifier_version="v_test",
        classifier_confidence=0.9,
    )


# ── DB-gated: the sweep ───────────────────────────────────────────────────────


@_NEEDS_DB
async def test_creates_tracking_row_and_excluded_from_ingest_plan(
    db_session: Any, caplog: Any
) -> None:
    # INFO level so the final ``logger.info(... extra=...)`` actually builds a
    # LogRecord — guards against extra keys colliding with reserved LogRecord
    # attributes (e.g. ``created``), which raises KeyError only when INFO is
    # enabled (prod) and is silent otherwise (the bug that 500'd in prod).
    caplog.set_level(logging.INFO, logger="job_assist.services.applied_companies")
    db_session.add_all(
        [
            _confirmation("Thank you for applying to Brightwheel"),
            _confirmation("Thank you for applying at Brightwheel!"),
        ]
    )
    await db_session.commit()

    report = await sync_applied_companies(db_session)
    assert report.created == 1
    assert report.linked == 2

    tc = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.source == "applied"))
    ).scalar_one()
    assert tc.name == "Brightwheel"
    assert tc.tier is None
    assert tc.ats_handle is None

    # THE cron driver must never surface it.
    plan = await get_ingest_plan(db_session)
    assert all("brightwheel" not in p["handle"].lower() for p in plan)
    assert plan == []  # only the tracking row exists


@_NEEDS_DB
async def test_existing_target_annotated_not_duplicated(db_session: Any) -> None:
    plaid = TargetCompany(name="Plaid", tier=1, ats="greenhouse", ats_handle="plaid")
    db_session.add(plaid)
    await db_session.flush()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    db_session.add_all(
        [
            _confirmation("Thank you for applying to Plaid", received_at=base),
            _confirmation("Thank you for applying to Plaid", received_at=base + timedelta(days=2)),
        ]
    )
    await db_session.commit()

    report = await sync_applied_companies(db_session)
    assert report.created == 0
    assert report.linked == 2

    # No duplicate Plaid row.
    n_plaid = (
        await db_session.execute(
            select(func.count()).select_from(TargetCompany).where(TargetCompany.name == "Plaid")
        )
    ).scalar_one()
    assert n_plaid == 1

    # /companies surfaces the activity through the endpoint.
    resp = await list_companies(db_session)
    row = next(it for it in resp["items"] if it["name"] == "Plaid")
    assert row["application_count"] == 2
    assert row["last_applied_at"] is not None
    assert row["source"] == "curated"


@_NEEDS_DB
async def test_singleton_suggested_not_created(db_session: Any) -> None:
    db_session.add(_confirmation("Thank you for applying to OneOff Labs"))
    await db_session.commit()

    report = await sync_applied_companies(db_session)
    assert report.created == 0
    assert report.linked == 0
    assert report.suggested == [{"name": "OneOff Labs", "count": 1}]

    # Nothing committed.
    n = (await db_session.execute(select(func.count()).select_from(TargetCompany))).scalar_one()
    assert n == 0


@_NEEDS_DB
async def test_links_more_than_commit_batch_in_one_group(db_session: Any) -> None:
    """Regression: a group with > the 25-row commit batch must complete. The
    intra-loop commit expires ORM objects, so the code must not read tc.id off
    an expired instance afterward (MissingGreenlet 500 in prod). 30 events for
    one company straddle the commit boundary."""
    db_session.add_all([_confirmation("Thank you for applying to BatchCo") for _ in range(30)])
    await db_session.commit()

    report = await sync_applied_companies(db_session)
    assert report.created == 1
    assert report.linked == 30

    linked = (
        await db_session.execute(
            select(func.count())
            .select_from(OutcomeEvent)
            .where(OutcomeEvent.target_company_id.is_not(None))
        )
    ).scalar_one()
    assert linked == 30


@_NEEDS_DB
async def test_normalization_matches_legal_suffix_variant(db_session: Any) -> None:
    db_session.add(TargetCompany(name="Plaid", tier=1, ats="greenhouse", ats_handle="plaid"))
    await db_session.flush()
    db_session.add_all(
        [
            _confirmation("Thank you for applying to Plaid Inc."),
            _confirmation("Thank you for applying to Plaid Inc."),
        ]
    )
    await db_session.commit()

    report = await sync_applied_companies(db_session)
    # "Plaid Inc." normalises to "plaid" → matches existing → link, no new row.
    assert report.created == 0
    assert report.linked == 2
    total = (await db_session.execute(select(func.count()).select_from(TargetCompany))).scalar_one()
    assert total == 1


@_NEEDS_DB
async def test_ingest_plan_excludes_source_applied_even_if_ingestable(db_session: Any) -> None:
    """The explicit ``source != 'applied'`` guard: a (malformed) applied row that
    otherwise looks ingestable (tier + handle set) is still excluded."""
    db_session.add(
        TargetCompany(
            name="Weird Applied",
            tier=1,
            ats="greenhouse",
            ats_handle="weird-applied",
            source="applied",
        )
    )
    await db_session.commit()
    plan = await get_ingest_plan(db_session)
    assert all(p["handle"] != "weird-applied" for p in plan)


@_NEEDS_DB
async def test_ensure_shell_company_links_to_existing_tracking_name(db_session: Any) -> None:
    """Edge case: a discovered handle title-casing onto an existing tracking
    name must LINK (attach the handle), not collide on UNIQUE(name)."""
    db_session.add(
        TargetCompany(name="Goldman Sachs", tier=None, ats_handle=None, source="applied")
    )
    await db_session.commit()

    inserted = await _ensure_shell_company(db_session, ats="greenhouse", handle="goldman-sachs")
    assert inserted is False  # linked, not inserted

    rows = (
        (
            await db_session.execute(
                select(TargetCompany).where(TargetCompany.name == "Goldman Sachs")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # no duplicate
    assert rows[0].ats_handle == "goldman-sachs"
