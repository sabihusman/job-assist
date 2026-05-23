"""add_fit_score_columns_to_job_posting

Adds three nullable columns to ``job_posting`` for the PR #56 heuristic
fit-scoring model:

  fit_score INTEGER NULL          -- 0-100 composite score
  scored_at TIMESTAMPTZ NULL      -- timestamp of last scoring pass
  scorer_version TEXT NULL        -- e.g. "v1_heuristic" (PR #56)

Plus an index ``idx_job_posting_fit_score_desc_nulls_last`` so the
future "Best fit" sort (PR #57) reads the top-scored postings without a
sort over the whole table.

All three columns are nullable — existing rows start NULL and are
backfilled via ``POST /admin/score/sweep``. The same pattern PR #48
used for ``classified_at`` / ``classifier_version``.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-05-29 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("fit_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("scorer_version", sa.Text(), nullable=True),
    )
    # DESC NULLS LAST so the future "Best fit" sort reads the top-scored
    # rows first and NULL-scored (unscored) rows trail. Postgres B-tree
    # indexes can't be DESC by default — declare it explicitly.
    op.execute(
        "CREATE INDEX idx_job_posting_fit_score_desc_nulls_last "
        "ON job_posting (fit_score DESC NULLS LAST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_job_posting_fit_score_desc_nulls_last")
    op.drop_column("job_posting", "scorer_version")
    op.drop_column("job_posting", "scored_at")
    op.drop_column("job_posting", "fit_score")
