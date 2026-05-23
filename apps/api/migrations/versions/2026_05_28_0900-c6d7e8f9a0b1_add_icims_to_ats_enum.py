"""add_icims_to_ats_enum

Extends the ``ats_type`` PostgreSQL enum with the value ``'icims'`` for
PR #55's iCIMS ATS adapter.

PostgreSQL's ``ALTER TYPE ... ADD VALUE`` cannot run inside a
transaction block — Alembic's ``autocommit_block()`` is the canonical
idiom. ``IF NOT EXISTS`` keeps the migration idempotent on re-runs.

Downgrade is a no-op: PostgreSQL doesn't support removing a value from
an enum without recreating the type and rewriting every column that
uses it. We're not going to do that for one value.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-05-28 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ats_type ADD VALUE IF NOT EXISTS 'icims'")


def downgrade() -> None:
    # PG does not support removing enum values. Operators can ignore the
    # 'icims' value if rolling back — it will simply have zero rows.
    pass
