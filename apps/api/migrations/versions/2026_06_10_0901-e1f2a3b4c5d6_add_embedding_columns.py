"""add_embedding_columns (semantic ranking slice 1, re-land)

Adds pgvector embedding storage WITHOUT any scoring change — populate + the
read-only validation gate only (see ``services/embeddings.py`` and
``GET /admin/embeddings/nearest``). Nothing reads the vectors for ranking
(that's slice 2).

Chains off the extension migration (d0e1f2a3b4c5) so the ``vector`` type is
guaranteed present before these Vector columns are added — and so an extension
failure can't roll these adds back (the #104 lesson; they're separate txns).

job_posting (7 cols, all nullable / zero-default — existing rows start
un-embedded, byte-identical on every read path):
  jd_embedding Vector(768), embedded_at, embedding_model_version,
  jd_text_hash_embedded, embedded_source, embedding_error,
  embedding_attempt_count.

operator_profile (3 cols): looking_for_embedding Vector(768),
  looking_for_embedding_hash, looking_for_embedded_at.

NO index — HNSW (vector_cosine_ops) deferred to a post-populate follow-up; a
flat cosine scan over ~1.5k rows is sub-millisecond and the slice-1 validation
query needs none.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-10 09:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 768


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("jd_embedding", Vector(_EMBEDDING_DIM), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("embedding_model_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("jd_text_hash_embedded", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("embedded_source", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("embedding_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column(
            "embedding_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.add_column(
        "operator_profile",
        sa.Column("looking_for_embedding", Vector(_EMBEDDING_DIM), nullable=True),
    )
    op.add_column(
        "operator_profile",
        sa.Column("looking_for_embedding_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "operator_profile",
        sa.Column("looking_for_embedded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("operator_profile", "looking_for_embedded_at")
    op.drop_column("operator_profile", "looking_for_embedding_hash")
    op.drop_column("operator_profile", "looking_for_embedding")

    op.drop_column("job_posting", "embedding_attempt_count")
    op.drop_column("job_posting", "embedding_error")
    op.drop_column("job_posting", "embedded_source")
    op.drop_column("job_posting", "jd_text_hash_embedded")
    op.drop_column("job_posting", "embedding_model_version")
    op.drop_column("job_posting", "embedded_at")
    op.drop_column("job_posting", "jd_embedding")
    # Leave the ``vector`` extension installed (its own migration owns it).
