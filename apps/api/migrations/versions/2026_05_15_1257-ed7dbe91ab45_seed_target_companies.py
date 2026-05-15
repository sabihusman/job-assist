"""seed_target_companies

Revision ID: ed7dbe91ab45
Revises: 7b89ad40468f
Create Date: 2026-05-15 12:57:05.714645

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed7dbe91ab45"
down_revision: str | Sequence[str] | None = "7b89ad40468f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# fmt: off
_ROWS: list[dict] = [
    # Tier 1 — wealthtech / fintech challengers
    {"name": "Addepar",                        "ats": "unknown", "tier": 1},
    {"name": "Orion Advisor Solutions",         "ats": "unknown", "tier": 1},
    {"name": "Envestnet",                       "ats": "unknown", "tier": 1},
    {"name": "iCapital",                        "ats": "unknown", "tier": 1},
    {"name": "Betterment",                      "ats": "unknown", "tier": 1},
    {"name": "MeridianLink",                    "ats": "unknown", "tier": 1},
    {"name": "Nymbus",                          "ats": "unknown", "tier": 1},
    {"name": "Q2 Holdings",                     "ats": "unknown", "tier": 1},
    {"name": "Cetera Financial Group",          "ats": "unknown", "tier": 1},
    {"name": "Axos Bank",                       "ats": "unknown", "tier": 1},
    # Tier 2 — FS incumbents
    {"name": "Wellmark Blue Cross Blue Shield", "ats": "unknown", "tier": 2},
    {"name": "Principal Financial Group",       "ats": "unknown", "tier": 2, "role_filter": "non_pm_only"},
    {"name": "Capital One",                     "ats": "workday", "tier": 2},
    {"name": "Charles Schwab",                  "ats": "unknown", "tier": 2},
    {"name": "Fidelity Investments",            "ats": "unknown", "tier": 2},
    {"name": "Morgan Stanley Wealth Management","ats": "unknown", "tier": 2},
    {"name": "Raymond James",                   "ats": "unknown", "tier": 2},
    {"name": "John Hancock / Manulife US",      "ats": "workday", "tier": 2},
    # Tier 3 — pure tech
    {"name": "Plaid",                           "ats": "unknown", "tier": 3},
    {"name": "Ramp",                            "ats": "unknown", "tier": 3},
    {"name": "Carta",                           "ats": "unknown", "tier": 3},
    {"name": "Notion",                          "ats": "unknown", "tier": 3},
    {"name": "Atlassian",                       "ats": "unknown", "tier": 3},
    {"name": "Justworks",                       "ats": "unknown", "tier": 3},
    {"name": "Mercury",                         "ats": "unknown", "tier": 3},
    {"name": "Brex",                            "ats": "unknown", "tier": 3},
    {"name": "Anthropic",                       "ats": "unknown", "tier": 3},
    {"name": "Linear",                          "ats": "unknown", "tier": 3},
    {"name": "Vanta",                           "ats": "unknown", "tier": 3},
    # Tier 4
    {"name": "Aon",                             "ats": "unknown", "tier": 4},
]
# fmt: on

_NAMES = [r["name"] for r in _ROWS]


def upgrade() -> None:
    """Seed initial target companies."""
    conn = op.get_bind()
    for row in _ROWS:
        role_filter = row.get("role_filter")
        conn.execute(
            sa.text(
                "INSERT INTO target_company "
                "(id, name, ats, tier, role_filter, created_at, updated_at) "
                "VALUES "
                "(gen_random_uuid(), :name, :ats, :tier, :role_filter, now(), now())"
            ),
            {
                "name": row["name"],
                "ats": row["ats"],
                "tier": row["tier"],
                "role_filter": role_filter,
            },
        )


def downgrade() -> None:
    """Remove seeded target companies."""
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM target_company WHERE name = ANY(:names)"),
        {"names": _NAMES},
    )
