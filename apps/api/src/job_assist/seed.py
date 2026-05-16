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
(neither updated nor duplicated). To update an existing row, edit it in
the DB directly or extend this script with a ``--upsert`` flag later.
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
_SEED_FIELDS = {"name", "tier", "ats", "ats_handle", "role_filter", "domain", "notes"}


def _project_row(row: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys and require the bare minimum (name, tier)."""
    projected = {k: v for k, v in row.items() if k in _SEED_FIELDS}
    if "name" not in projected or "tier" not in projected:
        raise ValueError(f"seed row missing required name/tier: {row!r}")
    projected.setdefault("ats", "unknown")
    return projected


async def seed_from_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> tuple[int, int]:
    """Insert each row whose ``name`` doesn't already exist.

    Returns ``(inserted, skipped)``. Commits before returning.
    """
    inserted = 0
    skipped = 0
    for raw in rows:
        row = _project_row(raw)
        existing = (
            await session.execute(select(TargetCompany).where(TargetCompany.name == row["name"]))
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        session.add(TargetCompany(**row))
        inserted += 1
    await session.commit()
    return inserted, skipped


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
            inserted, skipped = await seed_from_rows(session, rows)
    finally:
        await engine.dispose()

    print(f"Seeded {inserted} target_company rows; {skipped} already existed.")
    return inserted, skipped


def main() -> None:
    asyncio.run(seed_targets_from_file())


if __name__ == "__main__":
    main()
