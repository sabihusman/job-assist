"""deactivate dead broad-discovered lever handles (alpaca/avant/spendesk)

The ingest-visibility panel surfaced ``lever`` stuck in ``handle_not_found``.
Diagnosis: the curated lever target (Atlassian) is fine and excluded from the
daily cron; the failures come from the broad-ingest path probing three
broad-DISCOVERED lever handles that all 404 on lever's API:

  * alpaca   — no lever board; Alpaca recruits on Greenhouse (handle 'alpaca').
  * spendesk — no lever board; Spendesk is on Ashby (handle 'spendesk').
  * avant    — no public board at 'avant' on lever / greenhouse / ashby.

These are wrong guesses from broad discovery. The runner auto-deactivates a
handle after repeated not-found, but broad-ingest is manual (no cron), so they
stayed ``active`` and kept logging handle_not_found. This one-shot data fix
flips them to ``active = false`` so future broad runs skip them. (Their real
boards — e.g. Alpaca/Greenhouse's 56 roles — can be added as curated targets
separately if wanted; out of scope here.)

Data-only — no schema change, no extension. Chains off the similarity head.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-13 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE discovered_handle SET active = false "
        "WHERE ats = 'lever' AND handle IN ('alpaca', 'avant', 'spendesk')"
    )


def downgrade() -> None:
    # Symmetric reverse — re-activates only the three rows this migration
    # touched. (A downgrade resurrecting known-dead handles is undesirable in
    # practice; this exists purely for alembic reversibility.)
    op.execute(
        "UPDATE discovered_handle SET active = true "
        "WHERE ats = 'lever' AND handle IN ('alpaca', 'avant', 'spendesk')"
    )
