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

import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine


class SchemaBehindError(RuntimeError):
    """Raised when the live DB schema is behind the code's migration head."""


class SchemaGuardConfigError(RuntimeError):
    """Raised when the guard cannot locate the alembic migrations directory."""


def _source_relative_migrations() -> Path:
    """The migrations dir assuming the *source/editable* layout, where this
    file is ``apps/api/src/job_assist/db/schema_guard.py`` (parents[3] ==
    apps/api). Correct under an editable install / source checkout; WRONG under
    a non-editable install where this file lives in ``site-packages`` and the
    repo's ``migrations/`` is not shipped with the package — that mismatch
    crash-looped prod (resolved to ``…/python3.13/migrations``). Kept as a
    function so the resolver can be tested against the installed layout.
    """
    return Path(__file__).resolve().parents[3] / "migrations"


def _resolve_migrations_dir() -> Path:
    """Locate the alembic migrations dir ABSOLUTELY — independent of cwd and of
    where the package is installed.

    The repo's ``migrations/`` is NOT inside the importable package, so a
    ``__file__``-relative path only works in the source/editable layout. We try,
    in order, and return the first candidate that actually contains ``env.py``:

      1. ``ALEMBIC_MIGRATIONS_DIR`` env override (explicit escape hatch).
      2. source/editable layout (``_source_relative_migrations``).
      3. cwd and each ancestor — both ``<dir>/migrations`` and the monorepo
         ``<dir>/apps/api/migrations``. Covers the container (start.sh cd's to
         apps/api, so cwd/migrations matches) and running from the repo root.

    Validating on ``env.py`` is what makes this robust: a wrong candidate (e.g.
    the site-packages path) is skipped instead of handed to alembic, which would
    raise ``CommandError: Path doesn't exist``.
    """
    candidates: list[Path] = []

    override = os.getenv("ALEMBIC_MIGRATIONS_DIR")
    if override:
        candidates.append(Path(override))

    candidates.append(_source_relative_migrations())

    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        candidates.append(base / "migrations")
        candidates.append(base / "apps" / "api" / "migrations")

    for cand in candidates:
        if (cand / "env.py").is_file():
            return cand.resolve()

    raise SchemaGuardConfigError(
        "Could not locate the alembic migrations directory. Tried: "
        + ", ".join(str(c) for c in candidates[:6])
        + " … Set ALEMBIC_MIGRATIONS_DIR to override."
    )


def code_heads() -> set[str]:
    """The head revision id(s) declared by the in-repo migration scripts.

    Reads the filesystem only (no DB). Normally a single head; the
    single-head rule is enforced elsewhere, but we tolerate a set here.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(_resolve_migrations_dir()))
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
    "SchemaGuardConfigError",
    "assert_schema_at_head",
    "check_revision",
    "code_heads",
    "db_current_revision",
]
