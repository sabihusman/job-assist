"""add_implementation_role_family

Adds ``implementation`` to the ``role_family`` PG enum. Unlike the analyst
families (acceptable-but-discounted, ``c8e0f2a4b6d8``), implementation is a
PRIMARY track: it joins PREFERRED_FAMILIES in scoring.py — full role_family
sub-score, no composite cap. Covers Implementation Specialist / Consultant /
Analyst / Project Manager, Solutions Consultant, Onboarding Specialist,
Client Solutions Analyst — customer-facing product-deployment roles the v7
classifier bucketed into program_management or other.

Bestiary 2.6: ``ALTER TYPE … ADD VALUE`` cannot run inside a transaction
block — use the autocommit escape hatch (same pattern as the icims /
strategy_ops / analyst migrations).

Revision ID: e0a2c4b6d8f0
Revises: c8e0f2a4b6d8
Create Date: 2026-07-25 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e0a2c4b6d8f0"
down_revision: str | Sequence[str] | None = "c8e0f2a4b6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role_family ADD VALUE IF NOT EXISTS 'implementation'")


def downgrade() -> None:
    # PG does not support removing enum values. Rolling back simply leaves
    # the value unused (zero rows after a code rollback).
    pass
