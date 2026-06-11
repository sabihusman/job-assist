"""add_target_company_last_swept_at

Adds ``target_company.last_swept_at`` (feat/warm-path-ingest): when the Apify
(fantastic) path last swept this employer. Stamped per employer on every
fantastic sweep; the health monitor's ``warm_path_fresh`` check reads
``MAX(last_swept_at)`` over ``source='warm_path'`` rows against a ~9-day window
(weekly cadence + grace).

Nullable, no backfill — NULL simply means "never swept via the Apify path yet".

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "target_company",
        sa.Column("last_swept_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("target_company", "last_swept_at")
