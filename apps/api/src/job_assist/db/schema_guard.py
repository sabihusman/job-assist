"""Startup schema guard (feat/migration-deploy-gate) — Layer 2.

The deploy hole behind #104 and #107: code shipped ahead of its migration, so
the live DB lacked columns the ORM selected and every read 500'd. Layer 1
(``scripts/start.sh``: ``alembic upgrade head`` then ``exec uvicorn``) makes
migration atomic with serving. THIS module is the belt-and-suspenders Layer 2:
on startup, if the live DB's Alembic revision is behind the code's head, the
app REFUSES TO SERVE (raises) instead of returning 500s — so the deploy fails
and the prior healthy version keeps running.

Lightest reliable form: an Alembic **revision check** — compare the DB's
``alembic_version`` to the code's head revision(s) from the migration scripts.
Not reflect-and-compare (heavier, noisy on type/default mismatches); the
revision check is exact for the actual failure mode (DB behind head).

Host-agnostic: pure Python reading the in-repo migrations dir + one tiny query.
Travels to Hetzner/Docker unchanged.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

# apps/api/migrations — this file is apps/api/src/job_assist/db/schema_guard.py,
# so parents[3] == apps/api. Resolved absolutely so it works regardless of cwd.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


class SchemaBehindError(RuntimeError):
    """Raised when the live DB schema is behind the code's migration head."""


def code_heads() -> set[str]:
    """The head revision id(s) declared by the in-repo migration scripts.

    Reads the filesystem only (no DB). Normally a single head; the
    single-head rule is enforced elsewhere, but we tolerate a set here.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def check_revision(current: str | None, heads: set[str]) -> None:
    """Pure comparison: raise unless the DB's ``current`` revision is a head.

    ``current is None`` means the DB has no ``alembic_version`` row (never
    migrated) — that is "behind". Kept pure so the guard logic is unit-testable
    without a database.
    """
    if current not in heads:
        raise SchemaBehindError(
            "Database schema is behind the code: "
            f"alembic_version={current!r}, expected head(s)={sorted(heads)}. "
            "Refusing to start — run `alembic upgrade head` (the deploy "
            "entrypoint does this automatically; see scripts/start.sh)."
        )


async def db_current_revision(engine: AsyncEngine) -> str | None:
    """Read the live DB's current Alembic revision (or None if unmigrated)."""
    async with engine.connect() as conn:
        try:
            current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        except ProgrammingError:
            # alembic_version table doesn't exist yet → never migrated → behind.
            return None
        return str(current) if current is not None else None


async def assert_schema_at_head(engine: AsyncEngine) -> None:
    """Raise :class:`SchemaBehindError` if the live DB is behind the code head.

    Called from the FastAPI lifespan in production (see ``main.py``). A raise
    here aborts startup, so the deploy fails and the previous healthy version
    keeps serving — instead of serving 500s against a stale schema.
    """
    current = await db_current_revision(engine)
    check_revision(current, code_heads())


__all__ = [
    "SchemaBehindError",
    "assert_schema_at_head",
    "check_revision",
    "code_heads",
    "db_current_revision",
]
