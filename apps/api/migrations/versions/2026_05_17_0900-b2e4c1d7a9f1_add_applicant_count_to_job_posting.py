"""add_applicant_count_to_job_posting

Adds the nullable ``applicant_count`` column to ``job_posting`` so the
hard-rule filter (PR #23) can short-circuit postings that have already
attracted too many applicants. No ATS adapter populates this column
today — the field is intentionally optional and the filter tolerates
``NULL`` by skipping the check.

Revision ID: b2e4c1d7a9f1
Revises: a1f3c0b8e5d2
Create Date: 2026-05-17 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e4c1d7a9f1"
down_revision: str | Sequence[str] | None = "a1f3c0b8e5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("applicant_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_posting", "applicant_count")
