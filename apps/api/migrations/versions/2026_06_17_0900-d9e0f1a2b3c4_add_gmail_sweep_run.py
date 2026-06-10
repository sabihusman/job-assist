"""add_gmail_sweep_run

Creates the ``gmail_sweep_run`` audit table (feat/gmail-health-check) so the
health monitor can report whether Gmail ingestion is still running and how long
the last sweep took. The Gmail poll keeps no other run state (its watermark is
``MAX(outcome_event.received_at)``), so this is the only persisted timing record.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-17 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gmail_sweep_run",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("messages_listed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("outcomes_inserted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('poll','backfill')", name="ck_gmail_sweep_run_kind"),
        sa.CheckConstraint(
            "status IN ('running','success','failed')", name="ck_gmail_sweep_run_status"
        ),
    )
    op.create_index("idx_gmail_sweep_run_started_at", "gmail_sweep_run", ["started_at"])


def downgrade() -> None:
    op.drop_index("idx_gmail_sweep_run_started_at", table_name="gmail_sweep_run")
    op.drop_table("gmail_sweep_run")
