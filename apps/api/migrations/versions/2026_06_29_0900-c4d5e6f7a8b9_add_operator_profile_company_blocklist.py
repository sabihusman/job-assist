"""add_operator_profile_company_blocklist

Adds ``operator_profile.company_blocklist`` (feat/company-blocklist): a parallel
employer blocklist to ``staffing_firm_blocklist`` for dead-end employers. The
hard-rule filter matches it on a NORMALIZED (lowercased, non-alphanumeric
stripped) substring so company-name variants collapse.

Additive + NOT NULL with server_default '[]'::jsonb, then the singleton row
(id=1) is SEEDED with the initial defaults so the rule is active on deploy —
mirrors how ``staffing_firm_blocklist`` was seeded at table creation. Postings
matching the rule are marked ``hard_rule_failed='company_blocklist'`` (hidden by
default, recoverable via ``?include_filtered=true``). No row deletion.

A re-evaluation of existing rows (POST /admin/postings/reeval-hard-rules) is
needed for already-ingested postings to pick up the new rule.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-29 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mirrors ``triage.config.HardRuleConfig.company_blocklist`` at ship time.
# Kept inline so the migration stays runnable even if the dataclass moves.
_DEFAULT_COMPANY_BLOCKLIST = [
    "JPMorgan Chase",
    "J.P. Morgan",
    "JPMorgan",
    "Bank of America",
    "BofA",
    "Capital One",
]


def upgrade() -> None:
    op.add_column(
        "operator_profile",
        sa.Column(
            "company_blocklist",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # Seed the singleton row so the rule is active immediately (the column's
    # server_default '[]' would otherwise leave the existing id=1 row empty).
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE operator_profile SET company_blocklist = CAST(:blocklist AS jsonb) WHERE id = 1"
        ),
        {"blocklist": _json_array(_DEFAULT_COMPANY_BLOCKLIST)},
    )


def downgrade() -> None:
    op.drop_column("operator_profile", "company_blocklist")


# ── helpers ───────────────────────────────────────────────────────────────────


def _json_array(items: list[str]) -> str:
    """Render a list[str] as a JSON array literal suitable for ::jsonb cast."""
    import json

    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
