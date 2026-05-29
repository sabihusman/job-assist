"""add_hard_rule_eligibility_to_job_posting

Wires the previously-orphaned ``apply_hard_rules`` filter (PR #23/#43) into
the corpus. Adds two nullable columns to ``job_posting``:

  hard_rule_failed       TEXT NULL          -- the RuleName that failed, or
                                            -- NULL when the posting passed
  hard_rules_evaluated_at TIMESTAMPTZ NULL  -- when the eval last ran

Plus a partial index on the pass set so the default ``GET /postings`` filter
(``hard_rule_failed IS NULL``) is index-backed without bloating the index
with every filtered-out row.

Both columns nullable; existing rows start NULL (un-evaluated). They are
populated at ingest going forward and backfilled via
``POST /admin/postings/reeval-hard-rules`` (which also re-runs whenever the
operator changes their salary floor/ceiling in Settings). Storing the failed
RuleName (not just a boolean) keeps the filter debuggable — same reasoning as
``closed_at`` carrying a timestamp rather than a bare flag (Bestiary 5.18).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-05 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_posting",
        sa.Column("hard_rule_failed", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_posting",
        sa.Column("hard_rules_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index over the pass set only — the default /postings query
    # filters ``hard_rule_failed IS NULL``, and the failed rows never need
    # index coverage for that predicate.
    op.execute(
        "CREATE INDEX idx_job_posting_passes_hard_rules "
        "ON job_posting (hard_rule_failed) "
        "WHERE hard_rule_failed IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_job_posting_passes_hard_rules")
    op.drop_column("job_posting", "hard_rules_evaluated_at")
    op.drop_column("job_posting", "hard_rule_failed")
