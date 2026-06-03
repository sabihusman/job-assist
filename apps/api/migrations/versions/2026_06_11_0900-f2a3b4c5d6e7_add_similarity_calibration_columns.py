"""add_similarity_calibration_columns (semantic ranking slice 2a)

Calibration storage only — NO ranking change. Adds:
  job_posting.similarity_score  INTEGER NULL   -- calibrated 0-100 (PERCENT_RANK
                                               -- of cosine-to-profile across the
                                               -- embedded corpus). Materialized
                                               -- by POST /admin/embeddings/recalibrate,
                                               -- NOT by score_posting.
  operator_profile.similarity_weight FLOAT NOT NULL DEFAULT 0.0
                                               -- blend weight (0..1); 0 = off.
                                               -- Slice 2b's "Best fit (semantic)"
                                               -- sort reads it; this slice only
                                               -- stores it (default off).

Plain columns — NO extension, so it cannot fail on a vector/extension issue.
Chains off the embedding-columns head (e1f2a3b4c5d6); single head verified.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-11 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("similarity_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "operator_profile",
        sa.Column(
            "similarity_weight",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_profile", "similarity_weight")
    op.drop_column("job_posting", "similarity_score")
