"""list_fantastic_targets filter — feat/fantastic-domain-targeting.

The Apify path targets by DOMAIN, so it must include curated Workday/iCIMS
employers that have a domain even with a NULL ats_handle (Capital One / John
Hancock) — the gap that skipped them in the first run. DB-gated (CI postgres).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from job_assist.db.models import TargetCompany
from job_assist.services.fantastic_ingest import apify_domain_for, list_fantastic_targets

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── apify_domain_for (pure — no DB) ──────────────────────────────────────────


def test_apify_domain_for_prefers_override() -> None:
    tc = TargetCompany(
        name="John Hancock / Manulife US",
        domain="johnhancock.com",
        adapter_config={"apify_domain": "manulife.com"},
    )
    # Apify targets the parent domain; the company domain stays for Gmail.
    assert apify_domain_for(tc) == "manulife.com"
    assert tc.domain == "johnhancock.com"


def test_apify_domain_for_falls_back_to_domain() -> None:
    assert apify_domain_for(TargetCompany(name="A", domain="a.com", adapter_config=None)) == "a.com"
    assert (
        apify_domain_for(TargetCompany(name="B", domain="b.com", adapter_config={"site": "x"}))
        == "b.com"
    )


@_NEEDS_DB
@pytest.mark.asyncio
async def test_list_targets_includes_null_handle_with_domain(db_session: Any) -> None:
    db_session.add_all(
        [
            # The fix: NULL handle but has a domain → INCLUDED (Capital One shape).
            TargetCompany(
                name="CapOne",
                tier=2,
                ats="workday",
                ats_handle=None,
                domain="capitalone.com",
                source="curated",
            ),
            TargetCompany(
                name="HasHandle",
                tier=2,
                ats="icims",
                ats_handle="hh",
                domain="hh.com",
                source="curated",
            ),
            # No domain → EXCLUDED (Apify can't target it reliably).
            TargetCompany(
                name="NoDomain",
                tier=2,
                ats="workday",
                ats_handle="nd",
                domain=None,
                source="curated",
            ),
            # Wrong ATS → EXCLUDED (free adapter handles it).
            TargetCompany(
                name="GreenhouseCo",
                tier=2,
                ats="greenhouse",
                ats_handle="gh",
                domain="gh.com",
                source="curated",
            ),
            # Not curated → EXCLUDED.
            TargetCompany(
                name="BroadCo",
                tier=None,
                ats="workday",
                ats_handle=None,
                domain="broad.com",
                source="broad",
            ),
        ]
    )
    await db_session.commit()

    names = {t.name for t in await list_fantastic_targets(db_session)}
    assert "CapOne" in names  # the fix — NULL handle + domain
    assert "HasHandle" in names
    assert "NoDomain" not in names
    assert "GreenhouseCo" not in names
    assert "BroadCo" not in names


# ── Cohort selection (feat/warm-path-ingest) ─────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_warm_path_rows_excluded_from_curated_default(db_session: Any) -> None:
    """The DAILY cron's default (source='curated') must never sweep warm-path
    rows — the cohorts carry different cadences on a paid API."""
    db_session.add_all(
        [
            TargetCompany(
                name="CuratedCo",
                tier=2,
                ats="workday",
                domain="curated.com",
                source="curated",
            ),
            TargetCompany(
                name="John Deere",
                tier=None,
                ats="workday",
                domain="deere.com",
                source="warm_path",
            ),
        ]
    )
    await db_session.commit()

    curated = {t.name for t in await list_fantastic_targets(db_session)}
    assert curated == {"CuratedCo"}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_warm_path_cohort_selected_by_source(db_session: Any) -> None:
    """source='warm_path' selects exactly the warm-path rows (domain required,
    tier NULL fine, handle NULL fine)."""
    db_session.add_all(
        [
            TargetCompany(
                name="John Deere",
                tier=None,
                ats="workday",
                domain="deere.com",
                source="warm_path",
            ),
            TargetCompany(
                name="Mayo Clinic",
                tier=None,
                ats="workday",
                domain="mayoclinic.org",
                source="warm_path",
            ),
            # No domain → Apify can't target it → excluded.
            TargetCompany(name="NoDomainCo", tier=None, ats="workday", source="warm_path"),
            # Curated row → not in this cohort.
            TargetCompany(
                name="CuratedCo2",
                tier=2,
                ats="workday",
                domain="curated2.com",
                source="curated",
            ),
        ]
    )
    await db_session.commit()

    warm = {t.name for t in await list_fantastic_targets(db_session, source="warm_path")}
    assert warm == {"John Deere", "Mayo Clinic"}


# ── fix/ingest-lifecycle (audit HIGH #2): no last_swept_at stamp on failure ──


@_NEEDS_DB
@pytest.mark.asyncio
async def test_failed_sweep_does_not_stamp_last_swept_at(db_session: Any, monkeypatch: Any) -> None:
    """A FAILED per-employer run must leave last_swept_at stale, so a dead
    Sunday sweep trips warm_path_fresh instead of reading green while the
    employer's postings actually went un-refreshed."""
    from job_assist.services import fantastic_ingest as fi
    from job_assist.services.ingestion import IngestionService

    co = TargetCompany(
        name="DeadBoard", tier=None, ats="workday", domain="dead.com", source="warm_path"
    )
    db_session.add(co)
    await db_session.commit()

    class _Run:
        def __init__(self, status: str) -> None:
            self.status = status
            self.postings_fetched = 0
            self.postings_new = 0
            self.postings_updated = 0

    async def _fail(self: Any, adapter: Any, handle: Any, session: Any, **kw: Any) -> Any:
        return _Run("failed")

    monkeypatch.setattr(IngestionService, "ingest_source", _fail)
    await fi.ingest_curated_via_fantastic(db_session, token="tok", source="warm_path")
    await db_session.refresh(co)
    assert co.last_swept_at is None, "failed run must NOT stamp last_swept_at"

    async def _ok(self: Any, adapter: Any, handle: Any, session: Any, **kw: Any) -> Any:
        return _Run("success")

    monkeypatch.setattr(IngestionService, "ingest_source", _ok)
    await fi.ingest_curated_via_fantastic(db_session, token="tok", source="warm_path")
    await db_session.refresh(co)
    assert co.last_swept_at is not None, "successful run stamps last_swept_at"
