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
