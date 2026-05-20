"""add_jd_summary_columns

Adds four enrichment columns to ``job_posting`` for the JD summarization
service (PR #41):

  * jd_summary_markdown               TEXT NULL
  * jd_summary_enriched_at            TIMESTAMPTZ NULL
  * jd_summary_enrichment_error       TEXT NULL
  * jd_summary_enrichment_attempt_count INTEGER NOT NULL DEFAULT 0

Mirrors the shape of the company/division enrichment columns
(``description`` / ``enriched_at`` / ``enrichment_error`` /
``enrichment_attempt_count``) so the same six-status state machine
slots in over ``job_posting`` without further plumbing.

Additive only. Existing rows get NULL on the three nullable columns
and 0 on the attempt counter; the daily sweep at 08:30 UTC picks them
all up on the next run.

Revision ID: d5b9e8f3c2a1
Revises: c4e2f1b7a3d9
Create Date: 2026-05-25 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5b9e8f3c2a1"
down_revision: str | Sequence[str] | None = "c4e2f1b7a3d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("jd_summary_markdown", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("jd_summary_enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("jd_summary_enrichment_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column(
            "jd_summary_enrichment_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("job_posting", "jd_summary_enrichment_attempt_count")
    op.drop_column("job_posting", "jd_summary_enrichment_error")
    op.drop_column("job_posting", "jd_summary_enriched_at")
    op.drop_column("job_posting", "jd_summary_markdown")
