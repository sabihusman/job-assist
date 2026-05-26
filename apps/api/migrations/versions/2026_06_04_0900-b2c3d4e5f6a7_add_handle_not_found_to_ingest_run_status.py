"""add_handle_not_found_to_ingest_run_status

Follow-up to PR #63's Lever-zero-fetch investigation. Adds a new
value to the ``ingest_run_status`` Postgres enum so the orchestrator
can record "upstream returned 404 for the listing call" distinctly
from generic ``failed`` (which today covers network errors, parsing
failures, and now-also tenant-missing).

Without this distinction the operator can't tell from the run log
whether an ATS handle is stale vs the network was flaky — both
look identical. See Bestiary 5.9.

Bestiary 2.6: ``ALTER TYPE … ADD VALUE`` cannot run inside a
transaction block. The Alembic migration must use
``op.get_context().autocommit_block()`` to switch to autocommit
for the duration of the DDL.

Revision ID: b2c3d4e5f6a7
Revises: f9a0b1c2d3e4
Create Date: 2026-06-04 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ingest_run_status ADD VALUE IF NOT EXISTS 'handle_not_found'")


def downgrade() -> None:
    # Postgres does not support DROP VALUE on enums. The downgrade is
    # a no-op — the enum keeps the value, but no new rows will be
    # written with it once the ORM-side code rolls back. Pre-existing
    # ``handle_not_found`` rows continue to read correctly because
    # ``IngestRunStatus`` is a StrEnum and the value remains valid PG
    # data.
    pass
