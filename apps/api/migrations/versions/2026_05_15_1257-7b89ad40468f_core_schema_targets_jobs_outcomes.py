"""core_schema_targets_jobs_outcomes

Creates the 8 core tables for ingestion, applications, and outcomes.
Enum types are created first; tables are created in FK-dependency order.

Partial indexes and descending sort indexes are created explicitly after
table creation — Alembic autogenerate does not reliably emit these.

Note on updated_at: the columns carry SQLAlchemy's onupdate=func.now()
for ORM-layer updates.  A DB-level trigger can be added in a follow-up
migration if raw SQL updates need to be covered too.

Revision ID: 7b89ad40468f
Revises: 59e9badbd745
Create Date: 2026-05-15 12:57:03.442390

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7b89ad40468f"
down_revision: str | Sequence[str] | None = "59e9badbd745"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create enum types, then tables, then indexes."""

    # ------------------------------------------------------------------
    # Enum types
    # ------------------------------------------------------------------
    # create_type=False on every object so that op.create_table()'s
    # before_create event does NOT re-emit CREATE TYPE for these names.
    # We drive creation ourselves here, once, with checkfirst=True.
    sa.Enum(
        "greenhouse",
        "lever",
        "ashby",
        "workday",
        "other",
        "unknown",
        name="ats_type",
        create_type=False,
    ).create(op.get_bind(), checkfirst=True)

    sa.Enum("onsite", "hybrid", "remote", "unknown", name="remote_type", create_type=False).create(
        op.get_bind(), checkfirst=True
    )
    sa.Enum("hourly", "annual", "unknown", name="salary_period", create_type=False).create(
        op.get_bind(), checkfirst=True
    )
    sa.Enum(
        "intern",
        "apm",
        "pm",
        "senior_pm",
        "lead_pm",
        "principal_pm",
        "unknown",
        name="seniority_level",
        create_type=False,
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        "product_management",
        "product_owner",
        "product_marketing",
        "program_management",
        "other",
        name="role_family",
        create_type=False,
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum("ok", "partial", "failed", name="fetch_status", create_type=False).create(
        op.get_bind(), checkfirst=True
    )
    sa.Enum(
        "running", "success", "partial", "failed", name="ingest_run_status", create_type=False
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        "not_reviewed",
        "interested",
        "not_interested",
        "applied",
        "snoozed",
        name="application_status",
        create_type=False,
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        "application_confirmation",
        "recruiter_screen_invite",
        "phone_interview_invite",
        "video_interview_invite",
        "onsite_interview_invite",
        "panel_interview_invite",
        "offer",
        "rejection_pre_screen",
        "rejection_post_screen",
        "rejection_post_interview",
        "withdrawn",
        "unrelated",
        "unclassified",
        name="outcome_type",
        create_type=False,
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        "multiple_rejections",
        "culture_concern",
        "compensation_low",
        "recruiter_unprofessional",
        "other",
        name="closed_channel_reason",
        create_type=False,
    ).create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # target_company
    # ------------------------------------------------------------------
    op.create_table(
        "target_company",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "ats",
            sa.Enum(
                "greenhouse",
                "lever",
                "ashby",
                "workday",
                "other",
                "unknown",
                name="ats_type",
                create_type=False,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("ats_handle", sa.String(), nullable=True),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("role_filter", sa.String(50), nullable=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_target_company_name"),
    )
    op.create_index("idx_target_company_tier", "target_company", ["tier"])
    op.create_index("idx_target_company_ats", "target_company", ["ats"])

    # ------------------------------------------------------------------
    # job_posting
    # ------------------------------------------------------------------
    op.create_table(
        "job_posting",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_company_name", sa.String(), nullable=False),
        sa.Column(
            "target_company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("target_company.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("normalized_title", sa.String(), nullable=False),
        sa.Column("raw_title", sa.String(), nullable=False),
        sa.Column("location_raw", sa.String(), nullable=True),
        sa.Column("locations_normalized", postgresql.JSONB(), nullable=True),
        sa.Column(
            "remote_type",
            sa.Enum("onsite", "hybrid", "remote", "unknown", name="remote_type", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(3), nullable=True),
        sa.Column(
            "salary_period",
            sa.Enum("hourly", "annual", "unknown", name="salary_period", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "seniority_level",
            sa.Enum(
                "intern",
                "apm",
                "pm",
                "senior_pm",
                "lead_pm",
                "principal_pm",
                "unknown",
                name="seniority_level",
                create_type=False,
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "role_family",
            sa.Enum(
                "product_management",
                "product_owner",
                "product_marketing",
                "program_management",
                "other",
                name="role_family",
                create_type=False,
            ),
            nullable=False,
            server_default="other",
        ),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("jd_text_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "should_embed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.UniqueConstraint("content_hash", name="idx_job_posting_content_hash"),
    )
    op.create_index("idx_job_posting_jd_text_hash", "job_posting", ["jd_text_hash"])
    op.create_index(
        "idx_job_posting_first_seen_at",
        "job_posting",
        ["first_seen_at"],
        postgresql_ops={"first_seen_at": "DESC"},
    )
    op.create_index("idx_job_posting_target_company_id", "job_posting", ["target_company_id"])
    op.create_index(
        "idx_job_posting_should_embed",
        "job_posting",
        ["first_seen_at"],
        postgresql_where=sa.text("should_embed = true"),
    )

    # ------------------------------------------------------------------
    # posting_source
    # ------------------------------------------------------------------
    op.create_table(
        "posting_source",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_posting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ats",
            sa.Enum(
                "greenhouse",
                "lever",
                "ashby",
                "workday",
                "other",
                "unknown",
                name="ats_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("source_job_id", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("apply_url", sa.String(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("parser_version", sa.String(), nullable=False),
        sa.Column(
            "fetch_status",
            sa.Enum("ok", "partial", "failed", name="fetch_status", create_type=False),
            nullable=False,
            server_default="ok",
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("ats", "source_job_id", name="uq_posting_source_ats_job_id"),
    )
    op.create_index("idx_posting_source_job_posting_id", "posting_source", ["job_posting_id"])

    # ------------------------------------------------------------------
    # ingest_run
    # ------------------------------------------------------------------
    op.create_table(
        "ingest_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source",
            sa.Enum(
                "greenhouse",
                "lever",
                "ashby",
                "workday",
                "other",
                "unknown",
                name="ats_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "success",
                "partial",
                "failed",
                name="ingest_run_status",
                create_type=False,
            ),
            nullable=False,
            server_default="running",
        ),
        sa.Column("postings_fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("postings_new", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("postings_updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_traceback", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_ingest_run_started_at",
        "ingest_run",
        ["started_at"],
        postgresql_ops={"started_at": "DESC"},
    )
    op.create_index(
        "idx_ingest_run_source_started_at",
        "ingest_run",
        ["source", "started_at"],
        postgresql_ops={"started_at": "DESC"},
    )

    # ------------------------------------------------------------------
    # application_state
    # ------------------------------------------------------------------
    op.create_table(
        "application_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_posting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "not_reviewed",
                "interested",
                "not_interested",
                "applied",
                "snoozed",
                name="application_status",
                create_type=False,
            ),
            nullable=False,
            server_default="not_reviewed",
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snooze_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_application_state_status", "application_state", ["status"])
    op.create_index(
        "idx_application_state_snooze_until",
        "application_state",
        ["snooze_until"],
        postgresql_where=sa.text("snooze_until IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # outcome_event
    # ------------------------------------------------------------------
    op.create_table(
        "outcome_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_posting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("target_company.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email_message_id", sa.String(), nullable=False),
        sa.Column("email_thread_id", sa.String(), nullable=True),
        sa.Column("from_address", sa.String(), nullable=False),
        sa.Column("from_domain", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "outcome_type",
            sa.Enum(
                "application_confirmation",
                "recruiter_screen_invite",
                "phone_interview_invite",
                "video_interview_invite",
                "onsite_interview_invite",
                "panel_interview_invite",
                "offer",
                "rejection_pre_screen",
                "rejection_post_screen",
                "rejection_post_interview",
                "withdrawn",
                "unrelated",
                "unclassified",
                name="outcome_type",
                create_type=False,
            ),
            nullable=False,
            server_default="unclassified",
        ),
        sa.Column("classifier_version", sa.String(), nullable=False),
        sa.Column("classifier_confidence", sa.Float(), nullable=True),
        sa.Column("raw_snippet", sa.Text(), nullable=True),
        sa.UniqueConstraint("email_message_id", name="uq_outcome_event_email_message_id"),
    )
    op.create_index("idx_outcome_event_target_company_id", "outcome_event", ["target_company_id"])
    op.create_index("idx_outcome_event_job_posting_id", "outcome_event", ["job_posting_id"])
    op.create_index("idx_outcome_event_outcome_type", "outcome_event", ["outcome_type"])
    op.create_index(
        "idx_outcome_event_received_at",
        "outcome_event",
        ["received_at"],
        postgresql_ops={"received_at": "DESC"},
    )

    # ------------------------------------------------------------------
    # triage_result
    # ------------------------------------------------------------------
    op.create_table(
        "triage_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_posting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("verdict_text", sa.String(), nullable=True),
        sa.Column("rule_flags", postgresql.JSONB(), nullable=True),
        sa.Column("features", postgresql.JSONB(), nullable=True),
        sa.Column("profile_version", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_triage_result_score",
        "triage_result",
        ["score"],
        postgresql_ops={"score": "DESC"},
    )
    op.create_index(
        "idx_triage_result_created_at",
        "triage_result",
        ["created_at"],
        postgresql_ops={"created_at": "DESC"},
    )

    # ------------------------------------------------------------------
    # closed_channel
    # ------------------------------------------------------------------
    op.create_table(
        "closed_channel",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "target_company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("target_company.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "multiple_rejections",
                "culture_concern",
                "compensation_low",
                "recruiter_unprofessional",
                "other",
                name="closed_channel_reason",
                create_type=False,
            ),
            nullable=False,
            server_default="other",
        ),
        sa.Column("rejection_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("unsealed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_closed_channel_company_name", "closed_channel", ["company_name"])
    # Partial unique index: only one active (unsealed) record per company name.
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_closed_channel_company_name_active "
            "ON closed_channel (company_name) WHERE unsealed_at IS NULL"
        )
    )


def downgrade() -> None:
    """Drop tables in reverse FK-dependency order, then enum types."""

    # Partial unique index must be dropped before the table
    op.execute(sa.text("DROP INDEX IF EXISTS uq_closed_channel_company_name_active"))

    op.drop_table("closed_channel")
    op.drop_table("triage_result")
    op.drop_table("outcome_event")
    op.drop_table("application_state")
    op.drop_table("ingest_run")
    op.drop_table("posting_source")
    op.drop_table("job_posting")
    op.drop_table("target_company")

    # Drop enum types
    for enum_name in (
        "closed_channel_reason",
        "outcome_type",
        "application_status",
        "ingest_run_status",
        "fetch_status",
        "role_family",
        "seniority_level",
        "salary_period",
        "remote_type",
        "ats_type",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
