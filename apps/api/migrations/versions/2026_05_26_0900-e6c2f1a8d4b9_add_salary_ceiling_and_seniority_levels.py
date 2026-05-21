"""add_salary_ceiling_and_seniority_levels

Extends ``operator_profile`` (PR #43) with two operator-tunable filter
parameters:

  * ``salary_ceiling_usd`` (INTEGER NULL) — pair with the existing
    ``salary_floor_usd`` so the operator can set a range, not just a
    floor. NULL means "no ceiling".
  * ``seniority_levels_included`` (JSONB NULL) — list of
    ``SeniorityLevel`` enum values that the hard-rule filter should
    keep. NULL or empty means "include all levels" (no filter applied).

Both nullable + no server default so the existing seeded row keeps
working without changes. The hard-rule consumer treats NULL as "rule
disabled".

Revision ID: e6c2f1a8d4b9
Revises: d5b9e8f3c2a1
Create Date: 2026-05-26 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6c2f1a8d4b9"
down_revision: str | Sequence[str] | None = "d5b9e8f3c2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_profile",
        sa.Column("salary_ceiling_usd", sa.Integer(), nullable=True),
    )
    op.add_column(
        "operator_profile",
        sa.Column(
            "seniority_levels_included",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_profile", "seniority_levels_included")
    op.drop_column("operator_profile", "salary_ceiling_usd")
