"""add_per_company_cap_to_operator_profile

Makes the per-company surfacing cap operator-tunable. Adds a single plain
Integer column to the singleton operator_profile:

  per_company_cap  INTEGER NOT NULL DEFAULT 3   -- 0 = disabled (show all)

The cap was always enforced server-side (postings_query ROW_NUMBER CTE,
default 3) but unreachable from the UI — the frontend never sent the
``per_company_cap`` query param, so the operator was stuck at 3 and viable
roles ranked #4+ at prolific companies were silently suppressed. This column
is the persisted operator setting; the list / count / export endpoints fall
back to it when no explicit ``?per_company_cap`` override is supplied.

NOTE (verification standard, rule 4): this is a vector-free, extension-free
schema migration — NO ``CREATE EXTENSION``, so it cannot repeat the #104
failure mode (privilege-failed extension rolling back the column). It is a
plain additive column with a server_default, so the existing singleton row is
backfilled to 3 automatically and every pre-migration read is byte-identical.

Revision ID: c9d0e1f2a3b4
Revises: a7b8c9d0e1f2
Create Date: 2026-06-09 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_profile",
        sa.Column(
            "per_company_cap",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_profile", "per_company_cap")
