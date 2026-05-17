"""add_company_enrichment_columns

Adds four enrichment columns to ``target_company``:
  * description              TEXT NULL
  * enriched_at              TIMESTAMPTZ NULL
  * enrichment_error         TEXT NULL
  * enrichment_attempt_count INTEGER NOT NULL DEFAULT 0

Note: ``domain TEXT NULL`` already exists on the table from PR #1 (audited
pre-PR — see PR #27 description), so this migration does NOT add it.

No backfill. Existing rows stay NULL on the new nullable columns and zero
on the attempt counter; the daily enrichment sweep populates them on its
first run.

Revision ID: d6f1e3a8c2b5
Revises: c5d8e2f1a7b3
Create Date: 2026-05-19 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f1e3a8c2b5"
down_revision: str | Sequence[str] | None = "c5d8e2f1a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "target_company",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "target_company",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "target_company",
        sa.Column("enrichment_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "target_company",
        sa.Column(
            "enrichment_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    # Drop only the four columns this migration added; ``domain`` predates
    # the PR and must survive.
    op.drop_column("target_company", "enrichment_attempt_count")
    op.drop_column("target_company", "enrichment_error")
    op.drop_column("target_company", "enriched_at")
    op.drop_column("target_company", "description")
