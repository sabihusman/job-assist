"""add_posting_action

Creates the append-only ``posting_action`` event store for operator
triage decisions (PR #31).

Schema notes:
* ``action_type`` and ``reason`` are TEXT (not PG ENUM) so the
  vocabulary can evolve via code without an ALTER TYPE migration.
  CHECK constraints provide the same data-integrity guarantee.
* Cross-field rule "(action_type = 'not_interested') = (reason IS NOT NULL)"
  uses boolean equality so both directions ("reason missing on
  not_interested" and "reason set on any other action") are blocked
  by a single constraint.
* The (job_posting_id, created_at) BTREE index covers the dominant
  query pattern: "latest action for this posting" via LATERAL or
  DISTINCT ON.

Revision ID: a9c7e1b4f2d6
Revises: f8b3d5c9e1a4
Create Date: 2026-05-22 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c7e1b4f2d6"
down_revision: str | Sequence[str] | None = "f8b3d5c9e1a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "posting_action",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_posting_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snooze_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_type IN ('interested','not_interested','applied','snoozed','reset')",
            name="ck_posting_action_action_type",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ("
            "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
            "'wrong_stage','already_rejected_here','just_not_feeling_it')",
            name="ck_posting_action_reason",
        ),
        sa.CheckConstraint(
            "(action_type = 'not_interested') = (reason IS NOT NULL)",
            name="ck_posting_action_reason_required_for_not_interested",
        ),
        sa.CheckConstraint(
            "snooze_until IS NULL OR action_type = 'snoozed'",
            name="ck_posting_action_snooze_until_only_for_snoozed",
        ),
    )
    op.create_index(
        "ix_posting_action_job_posting_id_created_at_desc",
        "posting_action",
        ["job_posting_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_posting_action_job_posting_id_created_at_desc",
        table_name="posting_action",
    )
    op.drop_table("posting_action")
