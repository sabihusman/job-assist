"""Seed the ``discovered_handle`` table (Slice 2 trial of broad ingestion).

For the bounded trial this is a HAND-SEEDED list of ~50 known fintech /
wealthtech / financial-infra / fintech-SaaS company boards across
Greenhouse / Ashby / Lever — companies that fit the operator's PM
profile and are NOT in the curated 30. Slice 3 replaces this hand-seed
with a Common Crawl CDX scan that produces handles at scale; the storage
table + runner this script feeds stay unchanged.

Handles are best-effort board tokens. A few may 404 / return empty —
that's benign: the adapter raises HandleNotFoundError (recorded as
``status='handle_not_found'`` on the IngestRun) or returns ``[]``, the
runner bumps ``consecutive_empty_count``, and nothing enters the DB.
Over-inclusion here is fine; the trial measures the real hit rate.

Run locally against the configured DATABASE_URL::

    cd apps/api
    uv run python -m job_assist.scripts.discover_handles   # if packaged
    # or
    uv run python scripts/discover_handles.py

Or push to production via the seeding endpoint
``POST /admin/discovered-handles/seed`` (the body is the same list this
file holds), so the trial set lands on Railway without a DB round-trip.
The endpoint and this script share ``seed_discovered_handles``.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from job_assist.config import settings
from job_assist.services.broad_ingest import seed_discovered_handles

# ── The trial set: ~50 fintech/wealthtech/SaaS boards, NOT the curated 30 ──
#
# (ats, handle). Distribution mirrors real-world ATS share — Greenhouse
# dominant, Ashby for newer startups, Lever a minority.
TRIAL_HANDLES: tuple[tuple[str, str], ...] = (
    # Payments / money infra
    ("greenhouse", "stripe"),
    ("greenhouse", "adyen"),
    ("greenhouse", "checkoutcom"),
    ("ashby", "moderntreasury"),
    ("ashby", "unit"),
    ("ashby", "increase"),
    ("greenhouse", "lithic"),
    ("ashby", "highnote"),
    ("ashby", "column"),
    ("ashby", "sardine"),
    # Wealthtech / investing
    ("greenhouse", "public"),
    ("greenhouse", "m1finance"),
    ("lever", "alpaca"),
    ("greenhouse", "altruist"),
    ("greenhouse", "vise"),
    ("greenhouse", "farther"),
    ("ashby", "savvywealth"),
    ("ashby", "range"),
    # Neobank / lending
    ("greenhouse", "dave"),
    ("greenhouse", "moneylion"),
    ("greenhouse", "varomoney"),
    ("greenhouse", "current"),
    ("greenhouse", "upgrade"),
    ("greenhouse", "upstart"),
    ("lever", "avant"),
    ("greenhouse", "petal"),
    ("greenhouse", "novacredit"),
    ("greenhouse", "pinwheelapi"),
    # Payroll / spend / HR-fintech SaaS
    ("greenhouse", "gusto"),
    ("greenhouse", "deel"),
    ("greenhouse", "rippling"),
    ("greenhouse", "remote"),
    ("greenhouse", "bill"),
    ("greenhouse", "expensify"),
    ("greenhouse", "pleo"),
    ("lever", "spendesk"),
    ("greenhouse", "airbase"),
    ("greenhouse", "tipalti"),
    # Identity / risk / data infra
    ("greenhouse", "alloy"),
    ("greenhouse", "withpersona"),
    ("ashby", "middesk"),
    ("greenhouse", "fingerprint"),
    ("greenhouse", "ocrolus"),
    ("greenhouse", "codat"),
    # Crypto / digital assets
    ("greenhouse", "kraken"),
    ("greenhouse", "gemini"),
    ("greenhouse", "circle"),
    ("greenhouse", "fireblocks"),
    ("greenhouse", "chainalysis"),
    ("greenhouse", "anchoragedigital"),
)


async def _main() -> None:
    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as session:
            inserted, skipped = await seed_discovered_handles(session, list(TRIAL_HANDLES))
    finally:
        await engine.dispose()
    print(
        f"Seeded {inserted} discovered_handle rows; {skipped} already existed "
        f"({len(TRIAL_HANDLES)} in trial set)."
    )


if __name__ == "__main__":
    asyncio.run(_main())
