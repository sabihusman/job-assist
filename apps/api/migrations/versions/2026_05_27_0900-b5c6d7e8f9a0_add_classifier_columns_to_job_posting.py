"""add_classifier_columns_to_job_posting

Adds two nullable audit columns for the LLM reclassifier (PR #48):

  * ``classified_at``      (TIMESTAMPTZ NULL) — timestamp of the last LLM
    classification run against this row. NULL = never classified by the
    LLM sweep (still carries the ingest-time regex classification).
  * ``classifier_version`` (TEXT NULL) — version string of the classifier
    that produced the current ``role_family`` / ``seniority_level`` values.
    NULL = classified by the regex heuristic at ingest time. Non-null =
    the Gemini model + prompt version string written by the sweep.

Both nullable with no server default so every existing row keeps its
current values without a full-table UPDATE on deploy.

Revision ID: b5c6d7e8f9a0
Revises: f7d3e2b9c5a1
Create Date: 2026-05-27 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "f7d3e2b9c5a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("classifier_version", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_posting", "classifier_version")
    op.drop_column("job_posting", "classified_at")
