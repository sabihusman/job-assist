"""enable_vector_extension (semantic ranking slice 1, re-land)

EXTENSION LESSON (from the #104 outage): keep ``CREATE EXTENSION`` in its OWN
migration, separate from the column adds. Alembic runs each migration in its own
transaction, so an extension problem here can NEVER roll back the embedding
column adds (those live in the next migration, d0e1f2a3b4c5 -> e1f2a3b4c5d6).

The ``vector`` extension is already enabled in Supabase (0.8.0 confirmed), so
``CREATE EXTENSION IF NOT EXISTS`` is a safe no-op in production; this migration
also makes a fresh DB (CI / local / Hetzner) self-provision the extension.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-10 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Intentional no-op: never DROP the extension on downgrade — other objects
    # (and the embedding columns added by the next migration) may depend on it,
    # and it's harmless to leave installed.
    pass
