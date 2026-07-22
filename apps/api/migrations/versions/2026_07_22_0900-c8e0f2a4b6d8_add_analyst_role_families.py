"""add_analyst_role_families

Adds ``business_analyst`` and ``financial_analyst`` to the ``role_family`` PG
enum (business/financial-analyst expansion): PM/PO stay preferred; these two
new families are acceptable-but-discounted (see scoring.py ANALYST_FAMILIES).

Bestiary 2.6: ``ALTER TYPE … ADD VALUE`` cannot run inside a transaction
block — use the autocommit escape hatch (same pattern as the icims /
handle_not_found / strategy_ops migrations).

Revision ID: c8e0f2a4b6d8
Revises: b6d8e0f2a4c6
Create Date: 2026-07-22 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8e0f2a4b6d8"
down_revision: str | Sequence[str] | None = "b6d8e0f2a4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role_family ADD VALUE IF NOT EXISTS 'business_analyst'")
        op.execute("ALTER TYPE role_family ADD VALUE IF NOT EXISTS 'financial_analyst'")


def downgrade() -> None:
    # PG does not support removing enum values. Rolling back simply leaves
    # the values unused (zero rows after a code rollback).
    pass
