"""add_application_resume

feat/application-resume Phase 1. Creates ``application_resume`` — one tailored
resume per application, keyed on ``job_posting_id`` (UNIQUE). The resume is an
attribute of the application, not a global-library item. Additive: the old
``resume_version`` table + ``posting_action.resume_version_id`` FK stay in
place (dormant) for rollback safety.

  application_resume
    id             uuid pk
    job_posting_id uuid NOT NULL UNIQUE  FK → job_posting(id) ON DELETE CASCADE
    file_blob      bytea NULL
    file_name      text  NULL
    content_type   text  NULL
    resume_text    text  NULL
    angle          text  NULL
    label          text  NULL
    created_at     timestamptz NOT NULL default now()
    updated_at     timestamptz NOT NULL default now()

Backfill: for every applied posting_action tagged with a resume_version,
create one application_resume row (latest tag per posting wins), copying
angle / snapshot_text→resume_text / label. No files exist yet → blob NULL.
Preserves every existing resume entry under the new per-application model.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-06-14 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_resume",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_posting_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_blob", sa.LargeBinary(), nullable=True),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("resume_text", sa.Text(), nullable=True),
        sa.Column("angle", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
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
    )
    # One resume per application (and the conflict target for the upsert).
    op.create_index(
        "uq_application_resume_job_posting_id",
        "application_resume",
        ["job_posting_id"],
        unique=True,
    )

    # ── Backfill from the dormant resume_version library ──────────────────────
    # One row per applied posting that was tagged with a resume version; the
    # latest tag wins (DISTINCT ON + ORDER BY created_at DESC). gen_random_uuid
    # is core in PG13+. ON CONFLICT guards against a posting re-applied with
    # multiple tags.
    op.execute(
        """
        INSERT INTO application_resume
            (id, job_posting_id, resume_text, angle, label, created_at, updated_at)
        SELECT DISTINCT ON (pa.job_posting_id)
            gen_random_uuid(),
            pa.job_posting_id,
            rv.snapshot_text,
            rv.angle,
            rv.label,
            now(),
            now()
        FROM posting_action pa
        JOIN resume_version rv ON rv.id = pa.resume_version_id
        WHERE pa.resume_version_id IS NOT NULL
        ORDER BY pa.job_posting_id, pa.created_at DESC
        ON CONFLICT (job_posting_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("uq_application_resume_job_posting_id", table_name="application_resume")
    op.drop_table("application_resume")
