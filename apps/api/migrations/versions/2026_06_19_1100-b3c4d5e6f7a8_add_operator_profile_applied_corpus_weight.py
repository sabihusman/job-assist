"""add_operator_profile_applied_corpus_weight

Adds ``operator_profile.applied_corpus_weight`` (Phase A3, feat/applied-corpus-
boost): the tunable weight for the revealed-preference (applied-corpus
similarity) boost. DEFAULT 0.0 = OFF — at 0 the surgical boost is byte-identical
to the pre-A3 fit_score (no-op), so deploy changes no scores. Mirrors
``similarity_weight``.

Additive + NOT NULL with server_default '0'. No backfill needed.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-06-19 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_profile",
        sa.Column(
            "applied_corpus_weight",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_profile", "applied_corpus_weight")
