"""add_department_team_to_job_posting

Promotes department/team from ATS raw_payload into typed nullable columns
on ``job_posting`` so future queries (division discovery, filters, UI)
can pivot off them without per-ATS JSONB path arithmetic.

Two partial indexes cover the (target_company_id, department/team) lookup
that PR #28b's ``discover_divisions`` will run, scoped to rows where the
relevant column is populated (most won't be, on Greenhouse-only sources).

No backfill in this migration; existing rows stay NULL on both columns
and get filled by the daily adapter runs or by the explicit
``POST /admin/backfill/department-team`` endpoint added in this PR.

Revision ID: e7a9c4f2b6d8
Revises: d6f1e3a8c2b5
Create Date: 2026-05-20 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a9c4f2b6d8"
down_revision: str | Sequence[str] | None = "d6f1e3a8c2b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_posting", sa.Column("department", sa.Text(), nullable=True))
    op.add_column("job_posting", sa.Column("team", sa.Text(), nullable=True))

    op.create_index(
        "ix_job_posting_target_company_department",
        "job_posting",
        ["target_company_id", "department"],
        postgresql_where=sa.text("department IS NOT NULL"),
    )
    op.create_index(
        "ix_job_posting_target_company_team",
        "job_posting",
        ["target_company_id", "team"],
        postgresql_where=sa.text("team IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_posting_target_company_team", table_name="job_posting")
    op.drop_index("ix_job_posting_target_company_department", table_name="job_posting")
    op.drop_column("job_posting", "team")
    op.drop_column("job_posting", "department")
