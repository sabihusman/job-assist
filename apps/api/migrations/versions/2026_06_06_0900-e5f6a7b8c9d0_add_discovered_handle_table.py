"""add_discovered_handle_table

Slice 2 of the broad-ingestion expansion. Creates ``discovered_handle``
— the long-tail ``(ats, handle)`` inventory swept with the title
pre-filter (PR #96), distinct from the curated ``target_company``
rows.

Columns:
  ats                      TEXT NOT NULL         -- greenhouse|lever|ashby
  handle                   TEXT NOT NULL
  source                   TEXT NOT NULL default 'hand_seed_trial'
  discovered_at            TIMESTAMPTZ NOT NULL default now()
  last_ingested_at         TIMESTAMPTZ NULL
  consecutive_empty_count  INT NOT NULL default 0
  active                   BOOL NOT NULL default true

Indexes:
  uq_discovered_handle_ats_handle  UNIQUE (ats, handle)  -- idempotent seed
  idx_discovered_handle_active     partial, WHERE active = true

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-06 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovered_handle",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ats", sa.String(), nullable=False),
        sa.Column("handle", sa.String(), nullable=False),
        sa.Column(
            "source",
            sa.String(),
            nullable=False,
            server_default="hand_seed_trial",
        ),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_empty_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "uq_discovered_handle_ats_handle",
        "discovered_handle",
        ["ats", "handle"],
        unique=True,
    )
    # Partial index over the runner's hot path (active rows only).
    op.execute(
        "CREATE INDEX idx_discovered_handle_active "
        "ON discovered_handle (active) "
        "WHERE active = true"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_discovered_handle_active")
    op.drop_index("uq_discovered_handle_ats_handle", table_name="discovered_handle")
    op.drop_table("discovered_handle")
