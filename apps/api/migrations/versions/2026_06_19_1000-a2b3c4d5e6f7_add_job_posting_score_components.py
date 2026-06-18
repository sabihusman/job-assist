"""add_job_posting_score_components

Adds ``job_posting.score_components`` (Phase A1, feat/score-decomposition): a
JSONB breakdown of fit_score — per-sub-score value, weight, contribution,
renormalization (present/dropped), and which caps fired. Written alongside
fit_score by score_posting_decomposed; ``final`` always equals fit_score.

Additive + nullable, no in-migration backfill — NULL means "not yet decomposed".
Existing rows are populated by the components-only backfill endpoint (which
writes only when the recomputed decomposition reconciles to the stored
fit_score; drifted rows are left NULL and flagged).

Revision ID: a2b3c4d5e6f7
Revises: a9f1b2wellf6
Create Date: 2026-06-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "a9f1b2wellf6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("score_components", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_posting", "score_components")
