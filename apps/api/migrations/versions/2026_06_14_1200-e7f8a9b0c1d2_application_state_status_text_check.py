"""application_state_status_text_check

feat/manual-application-status Phase 1. Revives the dormant ``application_state``
table for manual lifecycle tracking. Converts ``status`` from the PG enum
``application_status`` (not_reviewed/interested/not_interested/applied/snoozed)
to TEXT + CHECK — the same deliberate pattern ``posting_action.action_type``
uses, so the vocabulary can evolve without the Alembic same-transaction
``ALTER TYPE ADD VALUE`` trap.

New vocabulary is the five manual lifecycle stages:
    applied, interview, offer, accepted, rejected
The vestigial triage values (not_reviewed/interested/not_interested/snoozed)
are dropped — a row exists ONLY once the operator sets a lifecycle status; the
triage state lives in ``posting_action``, not here.

Safe because the table is DORMANT: zero reads/writes shipped against it, so
there are no rows to migrate (confirmed — no endpoint touched it before this
PR). The conversion drops the column's enum-typed server_default first (you
can't ``ALTER TYPE`` a column while a default references the old type), casts
enum→text, then guards the new vocabulary with a CHECK. The PG enum type is
left in place (harmless; the baseline migration owns its lifecycle).

Revision ID: e7f8a9b0c1d2
Revises: c5d6e7f8a9b0
Create Date: 2026-06-14 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_application_state_status"
_LIFECYCLE_VALUES = ("applied", "interview", "offer", "accepted", "rejected")
# Legacy enum labels, kept only so the downgrade can cast text → enum safely.
_LEGACY_VALUES = ("not_reviewed", "interested", "not_interested", "applied", "snoozed")


def upgrade() -> None:
    from sqlalchemy import text

    conn = op.get_bind()
    # 1. Drop the enum-typed server default (blocks the type change otherwise).
    conn.execute(text("ALTER TABLE application_state ALTER COLUMN status DROP DEFAULT"))
    # 2. Enum → text. status::text yields the label string.
    conn.execute(
        text("ALTER TABLE application_state ALTER COLUMN status TYPE text USING status::text")
    )
    # 3. Guard the new lifecycle vocabulary.
    values_sql = ", ".join(f"'{v}'" for v in _LIFECYCLE_VALUES)
    conn.execute(
        text(
            f"ALTER TABLE application_state ADD CONSTRAINT {_CHECK_NAME} "
            f"CHECK (status IN ({values_sql}))"
        )
    )


def downgrade() -> None:
    from sqlalchemy import text

    conn = op.get_bind()
    # Drop the lifecycle CHECK, then cast text → the original enum. Lifecycle-
    # only values (interview/offer/accepted/rejected) have no legacy label, so
    # map them to 'applied' (the nearest legacy member) to keep the cast total.
    # Forward-only in prod; the dormant table is empty on the CI smoke-test.
    conn.execute(text(f"ALTER TABLE application_state DROP CONSTRAINT {_CHECK_NAME}"))
    legacy_sql = ", ".join(f"'{v}'" for v in _LEGACY_VALUES)
    conn.execute(
        text(
            "ALTER TABLE application_state ALTER COLUMN status TYPE application_status "
            f"USING (CASE WHEN status IN ({legacy_sql}) THEN status ELSE 'applied' END)"
            "::application_status"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE application_state ALTER COLUMN status "
            "SET DEFAULT 'not_reviewed'::application_status"
        )
    )
