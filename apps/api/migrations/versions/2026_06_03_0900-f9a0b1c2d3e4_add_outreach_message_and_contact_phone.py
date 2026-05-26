"""add_outreach_message_and_contact_phone

PR #52 — Contact CRUD + outreach logging foundation.

Adds the append-only ``outreach_message`` event table (mirrors the
``posting_action`` shape from PR #31: TEXT columns guarded by
CHECK constraints, latest-row reads via LATERAL) and a small
``contact.phone`` column that PR #52's PATCH endpoint exposes.

Why these in one migration: both are additive and land together as
PR #52. Splitting would mean two revisions with no test boundary
between them.

Indexes on ``outreach_message``:
* ``idx_outreach_message_contact_id_sent_at_desc`` — drives the
  most-recent-message-per-contact lookup that the follow-up cron
  (PR #54) will use. Same pattern as
  ``ix_posting_action_job_posting_id_created_at_desc``.
* ``uq_outreach_message_external_message_id`` (partial UNIQUE,
  WHERE ``external_message_id IS NOT NULL``) — Gmail dedup. PR #53
  will rely on this; declaring it UNIQUE here rather than as a
  follow-up migration avoids a "clean up dupes first" dance.
* ``idx_outreach_message_posting_id`` (partial, WHERE
  ``posting_id IS NOT NULL``) — posting-scoped outreach views.

FK choices:
* ``contact_id`` ON DELETE CASCADE — outreach rows are meaningless
  without their contact. PR #52 never hard-deletes a contact
  (archive sets ``archived_at`` instead), so CASCADE is a future
  guard for if hard-delete is ever exposed.
* ``posting_id`` ON DELETE SET NULL — historical context preserved
  if the posting is later deleted.

Revision ID: f9a0b1c2d3e4
Revises: a1b2c3d4e5f6
Create Date: 2026-06-03 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── contact.phone (additive, nullable) ───────────────────────────
    op.add_column(
        "contact",
        sa.Column("phone", sa.Text(), nullable=True),
    )

    # ── outreach_message table ───────────────────────────────────────
    op.create_table(
        "outreach_message",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "contact_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contact.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "posting_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_posting.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_message_id", sa.Text(), nullable=True),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "direction IN ('outbound','inbound')",
            name="ck_outreach_message_direction",
        ),
        sa.CheckConstraint(
            "channel IN ('email','linkedin','other')",
            name="ck_outreach_message_channel",
        ),
        sa.CheckConstraint(
            "source IN ('manual','gmail_auto')",
            name="ck_outreach_message_source",
        ),
    )

    # Lookup index — most-recent-message-per-contact reads (PR #54 cron
    # + the OutreachTimeline UI in PR #52 both hit this).
    op.create_index(
        "idx_outreach_message_contact_id_sent_at_desc",
        "outreach_message",
        ["contact_id", sa.text("sent_at DESC")],
    )

    # Partial UNIQUE on external_message_id. NULL allowed (operator's
    # manual entries don't have one); when present, the combination of
    # (channel, external_message_id) must be globally unique so PR #53
    # can safely upsert from Gmail without dupes.
    op.execute(
        "CREATE UNIQUE INDEX uq_outreach_message_external_message_id "
        "ON outreach_message (external_message_id) "
        "WHERE external_message_id IS NOT NULL"
    )

    # Partial index on posting_id for posting-scoped outreach history.
    op.execute(
        "CREATE INDEX idx_outreach_message_posting_id "
        "ON outreach_message (posting_id) "
        "WHERE posting_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_outreach_message_posting_id")
    op.execute("DROP INDEX IF EXISTS uq_outreach_message_external_message_id")
    op.drop_index(
        "idx_outreach_message_contact_id_sent_at_desc",
        table_name="outreach_message",
    )
    op.drop_table("outreach_message")
    op.drop_column("contact", "phone")
