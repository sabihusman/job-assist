"""add_too_many_open_apps_reason

Adds ``too_many_open_apps`` to the ``posting_action.reason`` CHECK constraint
(feat/company-app-awareness). This is a RELUCTANT pass reason — "nothing wrong
with the role, I just already have too many open applications at this company".
It is portfolio management, NOT a fit signal, so it is deliberately EXCLUDED from
the calibration fit-learning aggregates (see services/stats.py); the migration
only widens the stored vocabulary.

Drop + recreate ``ck_posting_action_reason`` with the expanded list, mirroring
the PR #43 (``too_senior`` / ``too_junior``) migration.

Downgrade reverses it — but only succeeds if no rows already carry
``too_many_open_apps`` (one-way in practice once the chip starts writing).

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-16 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASONS_NEW = (
    "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
    "'wrong_stage','already_rejected_here','just_not_feeling_it',"
    "'too_senior','too_junior','too_many_open_apps'"
)

_REASONS_OLD = (
    "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
    "'wrong_stage','already_rejected_here','just_not_feeling_it',"
    "'too_senior','too_junior'"
)


def upgrade() -> None:
    op.drop_constraint("ck_posting_action_reason", "posting_action", type_="check")
    op.create_check_constraint(
        "ck_posting_action_reason",
        "posting_action",
        f"reason IS NULL OR reason IN ({_REASONS_NEW})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_posting_action_reason", "posting_action", type_="check")
    op.create_check_constraint(
        "ck_posting_action_reason",
        "posting_action",
        f"reason IS NULL OR reason IN ({_REASONS_OLD})",
    )
