"""add_contact_table

Creates the generic ``contact`` table for the outreach pipeline (PR #39).

The schema is intentionally polymorphic: a contact may come from the
Tippie alumni directory, a LinkedIn outreach campaign, an inbound
recruiter, or a warm intro. Source-specific extras live in the
``source_metadata`` JSONB column rather than separate sibling tables —
the dominant query is "who do I know at company X" not "who came from
source Y", and JSONB keeps the read path single-table.

Dedup rules (case-insensitive, partial — NULLs allowed):
* ``email_primary``  via ``uq_contact_email_primary``
* ``linkedin_url``   via ``uq_contact_linkedin_url``

Reachability invariant:
* ``email_primary IS NOT NULL OR linkedin_url IS NOT NULL`` — a contact
  with no channel is unusable and the seed endpoint should reject it
  upstream rather than letting the row land.

``source_type`` is TEXT with a CHECK constraint (same pattern as
posting_action.action_type) so the vocabulary can grow without an
ALTER TYPE migration. Outreach state lives in PR #40's
``outreach_message`` table; we do not denormalise it onto this row.

Revision ID: c4e2f1b7a3d9
Revises: b3d8e9c4f5a1
Create Date: 2026-05-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e2f1b7a3d9"
down_revision: str | Sequence[str] | None = "b3d8e9c4f5a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("preferred_first_name", sa.Text(), nullable=True),
        sa.Column("email_primary", sa.Text(), nullable=True),
        sa.Column("email_secondary", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("current_employer", sa.Text(), nullable=True),
        sa.Column("current_position", sa.Text(), nullable=True),
        sa.Column("location_city", sa.Text(), nullable=True),
        sa.Column("location_state", sa.Text(), nullable=True),
        sa.Column("location_country", sa.Text(), nullable=True),
        sa.Column("location_metro", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("job_functions_of_interest", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("industries_of_interest", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "contact_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("contact_opt_in_topics", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "email_primary IS NOT NULL OR linkedin_url IS NOT NULL",
            name="ck_contact_has_channel",
        ),
        sa.CheckConstraint(
            "source_type IN ('tippie_alumni','linkedin_outreach','recruiter_inbound','warm_intro')",
            name="ck_contact_source_type",
        ),
    )

    # Partial LOWER() unique indexes — case-insensitive dedup, NULLs
    # allowed. Same pattern as closed_channel's "active row per company"
    # partial index but on a LOWER() expression. We use raw CREATE INDEX
    # because Alembic's op.create_index doesn't natively express the
    # combination of postgresql_where + a function expression.
    op.execute(
        "CREATE UNIQUE INDEX uq_contact_email_primary "
        "ON contact (LOWER(email_primary)) "
        "WHERE email_primary IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_contact_linkedin_url "
        "ON contact (LOWER(linkedin_url)) "
        "WHERE linkedin_url IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_contact_current_employer "
        "ON contact (LOWER(current_employer)) "
        "WHERE current_employer IS NOT NULL"
    )
    op.create_index("idx_contact_source_type", "contact", ["source_type"])


def downgrade() -> None:
    op.drop_index("idx_contact_source_type", table_name="contact")
    op.execute("DROP INDEX IF EXISTS idx_contact_current_employer")
    op.execute("DROP INDEX IF EXISTS uq_contact_linkedin_url")
    op.execute("DROP INDEX IF EXISTS uq_contact_email_primary")
    op.drop_table("contact")
