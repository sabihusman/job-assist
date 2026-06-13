"""DB-gated tests for the Wellfound ingest service (feat/wellfound-ingest).

The actor HTTP is never touched — ``WellfoundQuery`` is replaced by a stub that
returns canned RawPostings + telemetry, so these exercise the REAL pipeline
(company-shell materialization, ingest_source dedupe/classify/score, cross-source
company reuse) against CI's Postgres.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import func, select

from job_assist.adapters.base import RawPosting
from job_assist.adapters.wellfound import WellfoundFetchError, _source_job_id
from job_assist.db.models import JobPosting, PostingSource, TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _rec(company: str, *, jid: str, title: str = "Senior Product Manager") -> dict[str, Any]:
    # Real clearpath shape (Gate-1-confirmed): flat company_name, min_value/
    # max_value salary, unix live_start_at, company_badges for legitimacy.
    return {
        "id": jid,
        "title": title,
        "company_name": company,
        "company_slug": company.lower().replace(" ", "-").replace(",", "").replace(".", ""),
        "company_badges": ["Top Investors", "Actively Hiring"],
        "url": f"https://wellfound.com/jobs/{jid}",
        "description": "Own the product roadmap end to end. Real cash comp.",
        "live_start_at": 1780574400,
        "compensation_parsed": {
            "base_salary": {
                "min_value": 160000,
                "max_value": 200000,
                "currency": "USD",
                "unit": "YEARLY",
            }
        },
        "equity_parsed": {"min_value": 0.1, "max_value": 0.4},
        "location_names": ["Remote (US)"],
        "remote": True,
    }


def _install_stub(
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, Any]] | None = None,
    *,
    raise_exc: Exception | None = None,
) -> None:
    """Replace WellfoundQuery in the service module with a no-network stub."""

    class _StubQuery:
        def __init__(self, **_kw: Any) -> None:
            self.fetched = len(records or [])
            self.kept = len(records or [])
            self.skipped_quality = 0
            self.estimated_cost_usd = round(self.fetched * 0.00349, 4)
            self.cost_guard_tripped = False

        async def __aenter__(self) -> _StubQuery:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def run(self) -> list[RawPosting]:
            if raise_exc is not None:
                raise raise_exc
            return [
                RawPosting(source_job_id=_source_job_id(r), raw_payload=r) for r in (records or [])
            ]

    monkeypatch.setattr("job_assist.services.wellfound_ingest.WellfoundQuery", _StubQuery)


@_NEEDS_DB
@pytest.mark.asyncio
async def test_wellfound_materializes_shell_and_ingests(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.wellfound_ingest import ingest_wellfound

    _install_stub(
        monkeypatch,
        [
            _rec("Acme Labs", jid="wf-1"),
            _rec("Acme Labs", jid="wf-2", title="Group Product Manager"),
        ],
    )
    out = await ingest_wellfound(db_session, "tok")

    assert out["ok"] is True
    assert out["fetched"] == 2
    assert out["companies"] == 1
    assert out["postings_new"] == 2
    assert out["estimated_cost_usd"] > 0

    # A wellfound SHELL was created: source='wellfound', tier NULL, ats unknown.
    shell = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "Acme Labs"))
    ).scalar_one()
    assert shell.source == "wellfound"
    assert shell.tier is None
    assert str(shell.ats) in {"unknown", "ATS.unknown"}
    assert shell.last_swept_at is not None  # stamped on the non-failed run

    # Postings carry posting_source.ats='wellfound'.
    ps_count = (
        await db_session.execute(
            select(func.count()).select_from(PostingSource).where(PostingSource.ats == "wellfound")
        )
    ).scalar_one()
    assert ps_count == 2


@_NEEDS_DB
@pytest.mark.asyncio
async def test_wellfound_reuses_existing_company_by_normalized_name(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-source: a company that ALSO exists curated is REUSED (resolved by
    normalized name) — never a duplicate shell. This is what lets the curated
    and Wellfound copies of a role share one JobPosting via content_hash."""
    from job_assist.services.wellfound_ingest import ingest_wellfound

    curated = TargetCompany(
        name="Stripe", source="curated", tier=2, ats="greenhouse", ats_handle="stripe"
    )
    db_session.add(curated)
    await db_session.commit()
    curated_id = curated.id

    _install_stub(monkeypatch, [_rec("Stripe, Inc.", jid="wf-stripe-1")])
    out = await ingest_wellfound(db_session, "tok")
    assert out["ok"] is True

    # No DUPLICATE company — "Stripe, Inc." normalized to the curated "Stripe".
    stripe_rows = (
        (
            await db_session.execute(
                select(TargetCompany).where(TargetCompany.name.in_(["Stripe", "Stripe, Inc."]))
            )
        )
        .scalars()
        .all()
    )
    assert len(stripe_rows) == 1
    assert stripe_rows[0].id == curated_id
    assert stripe_rows[0].source == "curated"  # untouched

    # The Wellfound posting linked to the curated company.
    jp = (
        (
            await db_session.execute(
                select(JobPosting).where(JobPosting.target_company_id == curated_id)
            )
        )
        .scalars()
        .first()
    )
    assert jp is not None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_wellfound_skips_vendor_only_company_name(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.services.wellfound_ingest import ingest_wellfound

    # A junk/vendor company name normalizes to nothing → the record is skipped,
    # never materialized.
    _install_stub(monkeypatch, [_rec("greenhouse.io", jid="wf-x")])
    out = await ingest_wellfound(db_session, "tok")
    assert out["companies"] == 0
    assert out["skipped_no_company"] >= 1
    assert out["postings_new"] == 0


@_NEEDS_DB
@pytest.mark.asyncio
async def test_wellfound_soft_fails_on_actor_error(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-run actor failure soft-fails (ok=False) — never raises, so the
    cron can't crash."""
    from job_assist.services.wellfound_ingest import ingest_wellfound

    _install_stub(monkeypatch, raise_exc=WellfoundFetchError("actor down"))
    out = await ingest_wellfound(db_session, "tok")
    assert out["ok"] is False
    assert out["companies"] == 0
    assert "actor down" in out["error"]
