"""add_embedding_columns (semantic ranking slice 1)

Adds pgvector embedding storage WITHOUT any scoring change. This migration
only creates the extension + columns; nothing reads the vectors for ranking
yet (that is slice 2). See ``services/embeddings.py`` for the populate path
and ``GET /admin/embeddings/nearest`` for the read-only validation gate.

job_posting (7 columns, all nullable / zero-default — existing rows start
un-embedded and are byte-identical to pre-migration on every read path):

  jd_embedding            Vector(768)  -- text-embedding-004 output
  embedded_at             TIMESTAMPTZ  -- when the vector was last written
  embedding_model_version TEXT         -- e.g. "text-embedding-004"
  jd_text_hash_embedded   VARCHAR(64)  -- jd_text_hash at embed time; re-embed
                                       -- when it != the current jd_text_hash
  embedded_source         TEXT         -- "summary" | "jd_text" (debuggability)
  embedding_error         TEXT         -- last error string (NULL on success)
  embedding_attempt_count INTEGER      -- failed attempts since last success

operator_profile (3 columns):

  looking_for_embedding      Vector(768)  -- profile query vector
  looking_for_embedding_hash VARCHAR(64)  -- sha256(looking_for_text) at embed
                                          -- time; skip re-embed when unchanged
  looking_for_embedded_at    TIMESTAMPTZ

FLAG (a) — pgvector extension. ``CREATE EXTENSION IF NOT EXISTS vector`` runs
FIRST, before the Vector columns. It is idempotent. The connecting role must
have create-extension privilege; on Supabase the ``postgres`` role does. If
the migration's DATABASE_URL role is restricted, enable "vector" ONCE via the
Supabase dashboard (Database -> Extensions -> enable "vector") and the
IF NOT EXISTS becomes a no-op.

NO index in this migration: deferred to a post-populate follow-up (HNSW with
``vector_cosine_ops``). For ~1.5k rows a flat cosine scan is sub-millisecond,
so the slice-1 validation query needs no index, and HNSW is best built on
already-populated data.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-08 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 768


def upgrade() -> None:
    # Extension FIRST (idempotent) so the Vector columns below have a type.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

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
    # Leave the ``vector`` extension installed — other objects may depend on
    # it, and dropping an extension other migrations could rely on is unsafe.
