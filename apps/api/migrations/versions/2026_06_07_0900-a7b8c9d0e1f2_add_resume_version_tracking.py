"""add_resume_version_tracking

feat/resume-version-tracking. Creates ``resume_version`` (the tailored
resume variants) and adds a nullable ``posting_action.resume_version_id``
FK so an application can tag which resume was sent, for resume→outcome
analytics.

Both changes are additive + nullable — existing ``posting_action`` rows
(applied or otherwise) are untouched. The CHECK guard restricts the tag
to apply events (``resume_version_id IS NULL OR action_type='applied'``).

  resume_version
    id            uuid pk
    label         text NOT NULL UNIQUE
    angle         text NULL
    snapshot_text text NULL
    notes         text NULL
    created_at    timestamptz NOT NULL default now()

  posting_action
    + resume_version_id  uuid NULL  FK → resume_version(id) ON DELETE SET NULL
    + ck_posting_action_resume_only_for_applied

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-07 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resume_version",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("angle", sa.Text(), nullable=True),
        sa.Column("snapshot_text", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_resume_version_label", "resume_version", ["label"], unique=True
    )

    op.add_column(
        "posting_action",
        sa.Column(
            "resume_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resume_version.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_posting_action_resume_only_for_applied",
        "posting_action",
        "resume_version_id IS NULL OR action_type = 'applied'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_posting_action_resume_only_for_applied", "posting_action", type_="check"
    )
    op.drop_column("posting_action", "resume_version_id")
    op.drop_index("uq_resume_version_label", table_name="resume_version")
    op.drop_table("resume_version")
