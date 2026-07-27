"""add_reclassify_job

Creates the ``reclassify_job`` table (directive D-ASYNC-RESWEEP): the job-row
half of the job-row + poll pattern that makes POST /admin/reclassify/sweep
non-blocking. The endpoint inserts a row (status='queued') and returns 202
immediately; the in-process background worker updates processed/changed per
batch and finalizes status/finished_at; GET /admin/reclassify/jobs/{id} polls.

``status`` is TEXT with a CHECK guard rather than a PG enum — same rationale
as ``gmail_sweep_run`` (the other run-status audit table): keeps the
vocabulary evolvable without ALTER TYPE migrations.

Revision ID: f2b4d6a8c0e2
Revises: e0a2c4b6d8f0
Create Date: 2026-07-27 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "f2b4d6a8c0e2"
down_revision: str | Sequence[str] | None = "e0a2c4b6d8f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reclassify_job",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_reclassify_job_status",
        ),
    )
    # The poll endpoint reads by PK; ops queries read the most recent job.
    op.create_index("idx_reclassify_job_created_at", "reclassify_job", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_reclassify_job_created_at", table_name="reclassify_job")
    op.drop_table("reclassify_job")
