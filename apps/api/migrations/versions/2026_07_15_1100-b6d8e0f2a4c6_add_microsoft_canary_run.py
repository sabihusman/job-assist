"""add_microsoft_canary_run

Creates the ``microsoft_canary_run`` audit table (directive B, Phase 3) so the
health monitor can distinguish endpoint failure / schema drift / zero-results
anomaly for the Microsoft Careers adapter's dedicated live canary check.
Mirrors the ``gmail_sweep_run`` table (``d9e0f1a2b3c4``) — the only other
place this codebase persists a run-outcome row purely for the health monitor.

Revision ID: b6d8e0f2a4c6
Revises: a4b6c8d0e2f4
Create Date: 2026-07-15 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "b6d8e0f2a4c6"
down_revision: str | Sequence[str] | None = "a4b6c8d0e2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "microsoft_canary_run",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("matched_count", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('ok','endpoint_failure','schema_drift','zero_results')",
            name="ck_microsoft_canary_run_status",
        ),
    )
    op.create_index("idx_microsoft_canary_run_started_at", "microsoft_canary_run", ["started_at"])


def downgrade() -> None:
    op.drop_index("idx_microsoft_canary_run_started_at", table_name="microsoft_canary_run")
    op.drop_table("microsoft_canary_run")
