"""make_target_company_tier_nullable

Slice 2 of broad-ingestion. Broad-discovered company shells have no
pedigree tier — only the curated companies carry a hand-assigned 1-4.
Drops the NOT NULL constraint on ``target_company.tier`` so shell rows
can store NULL; the tier is derived from fit_score at display time
(Part D coalesce). NULL tier is already handled everywhere downstream
(``score_tier(None)→50``, ``postings_query`` sorts ``tier NULLS LAST``,
the read endpoint already emits ``tier=None`` for unmatched postings).

Pure constraint relaxation — no data rewrite, existing tier values
preserved. The downgrade re-adds NOT NULL, which would FAIL if any
NULL-tier rows exist by then; that's the correct safety behaviour (you
can't re-tighten the constraint while shells violate it).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-06 09:15:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("target_company", "tier", nullable=True)


def downgrade() -> None:
    # Re-adding NOT NULL fails if any NULL-tier (broad-ingest shell)
    # rows exist — intentional. Backfill or delete shells before
    # downgrading.
    op.alter_column("target_company", "tier", nullable=False)
