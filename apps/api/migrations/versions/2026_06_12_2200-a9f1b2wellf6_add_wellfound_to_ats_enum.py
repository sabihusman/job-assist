"""add_wellfound_to_ats_enum

Extends the ``ats_type`` PostgreSQL enum with ``'wellfound'`` for the
feat/wellfound-ingest adapter (Wellfound startup job board via the clearpath
Apify actor). Wellfound is the POSTING's source — it rides on
``posting_source.ats``; the discovered company shells carry ``ats='unknown'``.

PostgreSQL's ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction
block — ``autocommit_block()`` is the canonical Alembic idiom (same pattern as
the iCIMS ``c6d7e8f9a0b1`` migration). ``IF NOT EXISTS`` keeps it idempotent.

Downgrade is a no-op: PostgreSQL can't remove an enum value without recreating
the type and rewriting every column that uses it.

Revision ID: a9f1b2wellf6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-12 22:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9f1b2wellf6"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ats_type ADD VALUE IF NOT EXISTS 'wellfound'")


def downgrade() -> None:
    # PG does not support removing enum values. A rollback simply leaves the
    # 'wellfound' value present with zero rows.
    pass
