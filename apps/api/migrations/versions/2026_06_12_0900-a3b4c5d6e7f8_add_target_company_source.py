"""add target_company.source discriminator (applied-company tracking)

Adds a ``source`` discriminator so the Companies list can distinguish:
  * ``curated`` — hand-seeded targets with a pedigree tier (the daily cron's
    ingest source).
  * ``broad``   — thin shells auto-created by broad-ingest from a
    ``discovered_handle`` (tier NULL, ats_handle SET).
  * ``applied`` — tracking-only rows auto-created from Gmail
    ``application_confirmation`` outcomes (tier NULL, ats_handle NULL). These
    are NEVER ingested — they exist only so the Companies view reflects real
    application activity.

Plain TEXT column (like ``posting_action.action_type``) — keeps the vocabulary
evolvable without a PG enum. NO extension; chains off the similarity-calibration
head. Backfill: existing broad shells (tier NULL + ats_handle SET) → ``broad``;
everything else keeps the ``curated`` default.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-12 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "target_company",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'curated'"),
        ),
    )
    # Existing broad-ingest shells: tier NULL AND ats_handle present.
    op.execute(
        "UPDATE target_company SET source = 'broad' "
        "WHERE tier IS NULL AND ats_handle IS NOT NULL"
    )
    op.create_index("idx_target_company_source", "target_company", ["source"])


def downgrade() -> None:
    op.drop_index("idx_target_company_source", table_name="target_company")
    op.drop_column("target_company", "source")
