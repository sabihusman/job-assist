"""remove_target_company_seed_data

Empties the target_company table seeded by ``ed7dbe91ab45``. The seed
data is moving out of the migration tree and into a private, gitignored
JSON file (``apps/api/seeds/target_companies.json``) so the public repo
never carries operator-identifying company lists.

After this migration runs in production, run ``job-assist seed`` (or
``POST /admin/seed/target-companies``) once to repopulate the table
from the private JSON.

Revision ID: a1f3c0b8e5d2
Revises: ed7dbe91ab45
Create Date: 2026-05-16 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c0b8e5d2"
down_revision: str | Sequence[str] | None = "ed7dbe91ab45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The 30 names seeded by the previous migration. Listed here so both
# upgrade() (DELETE) and downgrade() (re-INSERT) operate on the same set.
#
# Tier/role_filter/ats values are duplicated from ed7dbe91ab45 so the
# downgrade exactly restores its end state — including the two Workday
# rows and Principal Financial Group's ``role_filter='non_pm_only'``.
_ROWS: list[dict[str, object]] = [
    # Tier 1
    {"name": "Addepar",                         "ats": "unknown", "tier": 1},
    {"name": "Orion Advisor Solutions",         "ats": "unknown", "tier": 1},
    {"name": "Envestnet",                       "ats": "unknown", "tier": 1},
    {"name": "iCapital",                        "ats": "unknown", "tier": 1},
    {"name": "Betterment",                      "ats": "unknown", "tier": 1},
    {"name": "MeridianLink",                    "ats": "unknown", "tier": 1},
    {"name": "Nymbus",                          "ats": "unknown", "tier": 1},
    {"name": "Q2 Holdings",                     "ats": "unknown", "tier": 1},
    {"name": "Cetera Financial Group",          "ats": "unknown", "tier": 1},
    {"name": "Axos Bank",                       "ats": "unknown", "tier": 1},
    # Tier 2
    {"name": "Wellmark Blue Cross Blue Shield", "ats": "unknown", "tier": 2},
    {"name": "Principal Financial Group",       "ats": "unknown", "tier": 2, "role_filter": "non_pm_only"},
    {"name": "Capital One",                     "ats": "workday", "tier": 2},
    {"name": "Charles Schwab",                  "ats": "unknown", "tier": 2},
    {"name": "Fidelity Investments",            "ats": "unknown", "tier": 2},
    {"name": "Morgan Stanley Wealth Management","ats": "unknown", "tier": 2},
    {"name": "Raymond James",                   "ats": "unknown", "tier": 2},
    {"name": "John Hancock / Manulife US",      "ats": "workday", "tier": 2},
    # Tier 3
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
]  # fmt: skip

_NAMES = [r["name"] for r in _ROWS]


def upgrade() -> None:
    """Delete the seeded target_company rows by name.

    Only the 30 originally-seeded names are removed; any rows added after
    seeding (e.g. via the seed script post-deploy) survive. This is
    deliberately scoped so an accidental re-run can't wipe out the
    operator's actual target list.
    """
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM target_company WHERE name = ANY(:names)"),
        {"names": _NAMES},
    )


def downgrade() -> None:
    """Re-insert the original 30 seeded rows (matches ed7dbe91ab45.upgrade())."""
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
