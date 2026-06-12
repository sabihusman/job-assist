"""Seed target_company rows from a JSON file.

Run locally:
    cd apps/api
    uv run python -m job_assist.seed

The JSON file at ``apps/api/seeds/target_companies.json`` is private (see
.gitignore + docs/RUNBOOK.md). For a public template, see
``apps/api/seeds/target_companies.example.json``.

The same logic is exposed via ``POST /admin/seed/target-companies`` so the
operator can push seed data straight to production without uploading a
file to the Railway container — the seed payload is the JSON body.

Idempotent: rows are matched by ``name``; existing rows are left alone
by default. Pass ``backfill_nullables=True`` (CLI or endpoint query
param) to patch currently-NULL columns on existing rows from the seed —
operator-supplied seed values overwrite *only* NULLs, never existing
values. Used by the feat/outcome-company-linking PR so the operator can
hand-fill ``domain`` on the existing 30 rows by re-POSTing the seed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from job_assist.config import settings
from job_assist.db.models import TargetCompany

# Allowed TargetCompany fields the seed file may set. Anything else is
# silently dropped so an over-eager JSON entry can't tamper with id/timestamps.
# feat/dm-employer-ingestion: ``adapter_config`` was MISSING here, so Workday
# tenants (which require {wd_number, site}) and iCIMS non-default URLs were
# silently dropped on seed — the reason curated Workday rows (Capital One,
# John Hancock) sit at null handles and never crawl.
_SEED_FIELDS = {
    "name",
    "tier",
    "ats",
    "ats_handle",
    "adapter_config",
    "role_filter",
    "domain",
    "notes",
    # feat/warm-path-ingest: rows may seed directly into a non-default cohort.
    # Without this, every seeded row lands as 'curated' (server default) and
    # the DAILY fantastic cron would sweep it — warm-path companies must be
    # born warm_path so they only ever ride the weekly sweep.
    "source",
}

# Mirror of main.py._CRAWL_CONFIG_SOURCES — the provenance vocabulary.
_SEED_SOURCES = {"curated", "broad", "deactivated", "applied", "warm_path"}


def _project_row(row: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys and require the bare minimum (name, tier)."""
    projected = {k: v for k, v in row.items() if k in _SEED_FIELDS}
    if "name" not in projected or "tier" not in projected:
        raise ValueError(f"seed row missing required name/tier: {row!r}")
    if "source" in projected and projected["source"] not in _SEED_SOURCES:
        raise ValueError(f"seed row source must be one of {sorted(_SEED_SOURCES)}: {row!r}")
    projected.setdefault("ats", "unknown")
    return projected


async def seed_from_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    *,
    backfill_nullables: bool = False,
) -> tuple[int, int, int]:
    """Insert each row whose ``name`` doesn't already exist.

    Returns ``(inserted, skipped, backfilled)``. Commits before returning.

    When ``backfill_nullables=True``, existing rows have their currently-
    NULL columns set from the seed (operator-supplied values overwrite
    NULLs only — never existing non-NULL values). ``backfilled`` counts
    rows that had ≥1 column updated this way. This is the path used by
    the feat/outcome-company-linking PR to fill ``domain`` on existing
    rows so the Gmail outcome→company matcher's domain path can fire.
    """
    inserted = 0
    skipped = 0
    backfilled = 0
    for raw in rows:
        row = _project_row(raw)
        existing = (
            await session.execute(select(TargetCompany).where(TargetCompany.name == row["name"]))
        ).scalar_one_or_none()
        if existing is not None:
            if backfill_nullables:
                changed = False
                # Patch nullable columns where the DB row is NULL and the
                # seed supplies a value. Only ``name`` is excluded (the match
                # key). fix(audit): ``tier`` used to be excluded on the stale
                # premise that it's NOT NULL — it has been nullable since the
                # broad-ingestion expansion, so a NULL-tier row silently never
                # got the seed's tier (and stayed outside the daily plan's
                # ``tier IS NOT NULL`` gate) while the response could still
                # claim the row was backfilled.
                for field in _SEED_FIELDS - {"name"}:
                    if field not in row:
                        continue
                    seed_value = row[field]
                    if seed_value is None:
                        continue
                    if getattr(existing, field) is None:
                        setattr(existing, field, seed_value)
                        changed = True
                if changed:
                    backfilled += 1
            skipped += 1
            continue
        session.add(TargetCompany(**row))
        inserted += 1
    await session.commit()
    return inserted, skipped, backfilled


def _default_seed_path() -> Path:
    """Path to the local seeds JSON file (private, gitignored)."""
    # apps/api/src/job_assist/seed.py  →  apps/api/seeds/target_companies.json
    return Path(__file__).resolve().parents[2] / "seeds" / "target_companies.json"


async def seed_targets_from_file(path: Path | None = None) -> tuple[int, int]:
    """CLI entry point. Loads the JSON file and runs ``seed_from_rows``."""
    seed_path = path or _default_seed_path()
    if not seed_path.exists():
        raise FileNotFoundError(
            f"Seed file not found: {seed_path}\n"
            f"Copy seeds/target_companies.example.json to seeds/target_companies.json "
            f"and edit it with your target list, then re-run."
        )

    rows: list[dict[str, Any]] = json.loads(seed_path.read_text())

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as session:
            inserted, skipped, backfilled = await seed_from_rows(session, rows)
    finally:
        await engine.dispose()

    print(
        f"Seeded {inserted} target_company rows; {skipped} already existed "
        f"({backfilled} backfilled)."
    )
    return inserted, skipped


def main() -> None:
    asyncio.run(seed_targets_from_file())


if __name__ == "__main__":
    main()
