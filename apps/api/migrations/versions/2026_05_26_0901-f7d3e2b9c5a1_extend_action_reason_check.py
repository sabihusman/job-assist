"""extend_action_reason_check

Adds ``too_senior`` and ``too_junior`` to the ``posting_action.reason``
CHECK constraint (PR #43). The constraint lives in
``ck_posting_action_reason`` (confirmed via the original
``add_posting_action`` migration); drop + recreate with the expanded
vocabulary.

Downgrade reverses the change — but only succeeds if no rows have
already been written with ``too_senior`` / ``too_junior``. The
two PR #43 frontend chips will start writing these values as soon
as the migration lands, so downgrade is one-way in practice. Same
caveat as any CHECK-tightening migration.

Revision ID: f7d3e2b9c5a1
Revises: e6c2f1a8d4b9
Create Date: 2026-05-26 09:01:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7d3e2b9c5a1"
down_revision: str | Sequence[str] | None = "e6c2f1a8d4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_posting_action_reason",
        "posting_action",
        type_="check",
    )
    op.create_check_constraint(
        "ck_posting_action_reason",
        "posting_action",
        "reason IS NULL OR reason IN ("
        "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
        "'wrong_stage','already_rejected_here','just_not_feeling_it',"
        "'too_senior','too_junior')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_posting_action_reason",
        "posting_action",
        type_="check",
    )
    op.create_check_constraint(
        "ck_posting_action_reason",
        "posting_action",
        "reason IS NULL OR reason IN ("
        "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
        "'wrong_stage','already_rejected_here','just_not_feeling_it')",
    )
