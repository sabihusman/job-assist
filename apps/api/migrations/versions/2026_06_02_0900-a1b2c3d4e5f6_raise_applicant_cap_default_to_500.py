"""raise_applicant_cap_default_to_500

Raise the ``operator_profile.applicant_cap`` column default 150 → 500
and update the existing seeded singleton row to match — provided the
operator hasn't already customized it.

Why now: the LinkedIn adapter ships in a future PR and will populate
``job_posting.applicant_count`` for the first time. Competitive
enterprise PM roles on LinkedIn regularly show 200-800 applicant
counts, so the original 150 default would have surfaced as a
near-universal drop on day one. Lifting the default before that
adapter lands means LinkedIn ingestion ships with a usable threshold
out of the gate rather than triggering a same-day tuning PR.

Idempotency:
* The ``UPDATE`` guard ``WHERE applicant_cap = 150`` only touches rows
  still on the old default. If the operator already PUT a custom
  value (e.g. 250) via ``/operator/profile``, that custom value is
  preserved.
* The column ``server_default`` change only affects NEWLY inserted
  rows — no impact on the existing row beyond the explicit UPDATE.

Downgrade caveat: the inverse UPDATE flips 500 → 150 only when the
current value is still 500. Operators who customized between
upgrade and downgrade keep their custom value. This is deliberate
— the migration's job is to manage the default, not the operator's
deliberate edits.

Revision ID: a1b2c3d4e5f6
Revises: e8f9a0b1c2d3
Create Date: 2026-06-02 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Future inserts get the new default.
    op.alter_column(
        "operator_profile",
        "applicant_cap",
        server_default="500",
    )
    # 2. Update the existing seeded singleton row IF it's still on the
    #    old default. Skip operator-customized values.
    op.execute("UPDATE operator_profile SET applicant_cap = 500 WHERE applicant_cap = 150")


def downgrade() -> None:
    op.alter_column(
        "operator_profile",
        "applicant_cap",
        server_default="150",
    )
    # Best-effort: only flip back rows still on the post-upgrade default.
    # Operator-customized values between upgrade and downgrade are preserved.
    op.execute("UPDATE operator_profile SET applicant_cap = 150 WHERE applicant_cap = 500")
