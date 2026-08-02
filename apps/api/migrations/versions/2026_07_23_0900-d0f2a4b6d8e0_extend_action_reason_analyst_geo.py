"""extend_action_reason_analyst_geo

Adds ``analyst_geo`` to the ``posting_action.reason`` CHECK constraint
(family-conditional geo directive). Mirrors the hard-rule ``RuleName``
``"analyst_geo"`` added to ``triage/hard_rules.py`` for
business_analyst/financial_analyst postings — this migration only widens the
stored ``posting_action.reason`` vocabulary (display-only for now; the
pass-reason feedback pipeline that would auto-write it from a hard-gate
failure is inert).

Drop + recreate ``ck_posting_action_reason`` with the expanded list,
mirroring the ``too_many_open_apps`` / ``too_senior``+``too_junior``
migrations.

Downgrade reverses it — but only succeeds if no rows already carry
``analyst_geo``.

Revision ID: d0f2a4b6d8e0
Revises: c8e0f2a4b6d8
Create Date: 2026-07-23 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d0f2a4b6d8e0"
down_revision: str | Sequence[str] | None = "c8e0f2a4b6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASONS_NEW = (
    "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
    "'wrong_stage','already_rejected_here','just_not_feeling_it',"
    "'too_senior','too_junior','too_many_open_apps','analyst_geo'"
)

_REASONS_OLD = (
    "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
    "'wrong_stage','already_rejected_here','just_not_feeling_it',"
    "'too_senior','too_junior','too_many_open_apps'"
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
