"""add_microsoft_to_ats_enum

Extends the ``ats_type`` PostgreSQL enum with ``'microsoft'`` for the native
Microsoft Careers adapter (directive B) — ``adapters/microsoft.py``, sourced
directly from ``apply.careers.microsoft.com``'s JSON API. Unlike Wellfound,
Microsoft is a single company (not a discovery feed), so it carries a real
``target_company`` row (``ats='microsoft'``, ``ats_handle='microsoft'``) and
rides the existing curated daily plan like Greenhouse/Lever/Ashby.

PostgreSQL's ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction
block — ``autocommit_block()`` is the canonical Alembic idiom here (same
pattern as the iCIMS ``c6d7e8f9a0b1`` and Wellfound ``a9f1b2wellf6``
migrations). ``IF NOT EXISTS`` keeps it idempotent.

Downgrade is a no-op: PostgreSQL can't remove an enum value without
recreating the type and rewriting every column that uses it.

Revision ID: a4b6c8d0e2f4
Revises: c4d5e6f7a8b9
Create Date: 2026-07-15 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a4b6c8d0e2f4"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ats_type ADD VALUE IF NOT EXISTS 'microsoft'")


def downgrade() -> None:
    # PG does not support removing enum values. A rollback simply leaves the
    # 'microsoft' value present with zero rows.
    pass
