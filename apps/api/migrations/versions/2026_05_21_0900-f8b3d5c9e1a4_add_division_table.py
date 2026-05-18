"""add_division_table

Creates the singleton-per-(company, dept, team) ``division`` table. Rows
are populated by the discovery sweep in PR #28b's
``division_enrichment.discover_divisions`` from distinct
``(target_company_id, department, team)`` tuples in ``job_posting``.

The unique constraint uses ``NULLS NOT DISTINCT`` (PG 15+) so two
postings with ``(X, "Eng", NULL)`` collapse into a single division row.
Cascade-deleted with the parent ``target_company``.

Revision ID: f8b3d5c9e1a4
Revises: e7a9c4f2b6d8
Create Date: 2026-05-21 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8b3d5c9e1a4"
down_revision: str | Sequence[str] | None = "e7a9c4f2b6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "division",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "target_company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("target_company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrichment_error", sa.Text(), nullable=True),
        sa.Column(
            "enrichment_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "target_company_id",
            "department",
            "team",
            name="uq_division_company_dept_team",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "idx_division_target_company_id",
        "division",
        ["target_company_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_division_target_company_id", table_name="division")
    op.drop_table("division")
