"""add_archived_at_and_target_company_to_contact

Additive migration (PR #51) — extends the ``contact`` table shipped in
PR #39 with two columns:

  archived_at        TIMESTAMPTZ NULL   — soft-delete marker
  target_company_id  UUID NULL          — FK to ``target_company(id)``,
                                          ON DELETE SET NULL

Also REPLACES the two partial UNIQUE indexes on email_primary and
linkedin_url so their predicate excludes archived rows. Without that
change, archiving a contact would leave their email/LinkedIn occupying
the unique slot, and a future re-ingestion of the same person (e.g.
they re-opt in to the directory after a year) would fail the unique
constraint silently. The dedup contract should align with the
visible-rows contract: only active rows count.

Existing rows are unaffected — both new columns are nullable, the FK
has ON DELETE SET NULL, and the unique indexes' new predicate is a
strict superset of the old one (every row currently in the table has
``archived_at IS NULL`` by definition since the column didn't exist).

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-05-30 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── New columns ──────────────────────────────────────────────────
    op.add_column(
        "contact",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contact",
        sa.Column(
            "target_company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("target_company.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── Replace the partial UNIQUE indexes ──────────────────────────
    # Add ``AND archived_at IS NULL`` to the predicate so soft-deleted
    # contacts free up their email/LinkedIn slot for re-ingestion.
    op.execute("DROP INDEX IF EXISTS uq_contact_email_primary")
    op.execute("DROP INDEX IF EXISTS uq_contact_linkedin_url")
    op.execute(
        "CREATE UNIQUE INDEX uq_contact_email_primary "
        "ON contact (LOWER(email_primary)) "
        "WHERE email_primary IS NOT NULL AND archived_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_contact_linkedin_url "
        "ON contact (LOWER(linkedin_url)) "
        "WHERE linkedin_url IS NOT NULL AND archived_at IS NULL"
    )

    # ── FK lookup index ─────────────────────────────────────────────
    # Partial on the non-NULL subset since most contacts won't have a
    # matched target_company_id today.
    op.execute(
        "CREATE INDEX idx_contact_target_company_id "
        "ON contact (target_company_id) "
        "WHERE target_company_id IS NOT NULL"
    )


def downgrade() -> None:
    # Restore the original (non-archived-aware) partial unique indexes.
    op.execute("DROP INDEX IF EXISTS idx_contact_target_company_id")
    op.execute("DROP INDEX IF EXISTS uq_contact_linkedin_url")
    op.execute("DROP INDEX IF EXISTS uq_contact_email_primary")
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
    op.drop_column("contact", "target_company_id")
    op.drop_column("contact", "archived_at")
