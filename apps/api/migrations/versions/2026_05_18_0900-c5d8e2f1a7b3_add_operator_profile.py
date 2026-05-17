"""add_operator_profile

Creates the singleton ``operator_profile`` table and seeds id=1 with the
current ``HardRuleConfig`` defaults so the row exists from the moment
the migration lands. The hard-rule filter still reads its hardcoded
config in PR #26; consumer switch is PR #29+.

Revision ID: c5d8e2f1a7b3
Revises: b2e4c1d7a9f1
Create Date: 2026-05-18 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c5d8e2f1a7b3"
down_revision: str | Sequence[str] | None = "b2e4c1d7a9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# These mirror ``triage.config.HardRuleConfig`` at the moment this PR ships.
# Kept inline (rather than imported) so the migration stays runnable even if
# the dataclass moves or is renamed in a later PR.
_DEFAULT_GEO_WHITELIST = [
    "Remote",
    "Des Moines",
    "NYC",
    "New York",
    "Austin",
    "San Francisco",
    "Bay Area",
    "Seattle",
    "Minneapolis",
    "Chicago",
]
_DEFAULT_STAFFING_FIRM_BLOCKLIST = [
    "Robert Half",
    "Aerotek",
    "Insight Global",
    "Apex Systems",
    "Beacon Hill",
    "TEKsystems",
    "Modis",
    "Randstad",
    "Kforce",
    "Adecco",
]


def upgrade() -> None:
    op.create_table(
        "operator_profile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column(
            "looking_for_text",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "role_keywords",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "geo_whitelist",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "salary_floor_usd",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("85000"),
        ),
        sa.Column(
            "applicant_cap",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("150"),
        ),
        sa.Column(
            "staffing_firm_blocklist",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
        sa.CheckConstraint("id = 1", name="ck_operator_profile_singleton"),
    )

    # Seed the singleton row with the HardRuleConfig defaults. The JSONB
    # values are cast via the ::jsonb suffix so the literal SQL stays
    # consumable from psql too.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO operator_profile (
                id,
                looking_for_text,
                role_keywords,
                geo_whitelist,
                salary_floor_usd,
                applicant_cap,
                staffing_firm_blocklist
            ) VALUES (
                1,
                '',
                '[]'::jsonb,
                CAST(:geo AS jsonb),
                85000,
                150,
                CAST(:blocklist AS jsonb)
            )
            """
        ),
        {
            "geo": _json_array(_DEFAULT_GEO_WHITELIST),
            "blocklist": _json_array(_DEFAULT_STAFFING_FIRM_BLOCKLIST),
        },
    )


def downgrade() -> None:
    op.drop_table("operator_profile")


# ── helpers ───────────────────────────────────────────────────────────────────


def _json_array(items: list[str]) -> str:
    """Render a list[str] as a JSON array literal suitable for ::jsonb cast."""
    import json

    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
