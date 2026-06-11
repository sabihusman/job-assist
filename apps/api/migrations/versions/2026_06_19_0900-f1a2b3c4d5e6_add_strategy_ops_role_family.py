"""add_strategy_ops_role_family

Adds ``strategy_ops`` to the ``role_family`` PG enum (feat/strategy-spine):
the MBA-grad-suited strategy family (Strategy & Operations, Corporate
Strategy, BizOps, Chief of Staff) at warm-path / off-domain employers.

Bestiary 2.6: ``ALTER TYPE … ADD VALUE`` cannot run inside a transaction
block — use the autocommit escape hatch (same pattern as the icims /
handle_not_found migrations).

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-06-19 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role_family ADD VALUE IF NOT EXISTS 'strategy_ops'")


def downgrade() -> None:
    # PG does not support removing enum values. Rolling back simply leaves
    # the value unused (zero rows after a code rollback).
    pass
