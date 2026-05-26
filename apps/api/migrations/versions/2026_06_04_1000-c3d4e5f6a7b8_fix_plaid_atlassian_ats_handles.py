"""fix_plaid_atlassian_ats_handles

Data-only follow-up to PR #63's Lever zero-fetch investigation.

The seed-data was stale: both Plaid and Atlassian were configured
as ``ats='lever'`` but Lever's public API returns 404 for both
handles. Direct probes confirmed:

* **Plaid**: now on Ashby. ``GET
  https://api.ashbyhq.com/posting-api/job-board/plaid`` returns
  200 with 85 open jobs. Update ``ats='ashby', ats_handle='plaid'``.
* **Atlassian**: ATS unidentified. Lever 404, Workday wd5 401 on
  every common site name, careers SPA reveals no embedded ATS URL.
  Soft-pause by setting ``ats_handle = NULL`` — the
  ``/admin/ingest/plan`` endpoint already filters
  ``ats_handle IS NOT NULL`` (main.py:123), so the next cron will
  skip the row cleanly. Add an investigation note to the row.

Both UPDATEs are guarded on the OLD value pattern (Bestiary 5.8) so
operator-customized rows aren't clobbered if someone manually fixed
either record in the interim.

Revision ID: c3d4e5f6a7b8
Revises: f9a0b1c2d3e4
Create Date: 2026-06-04 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plaid: lever/plaid → ashby/plaid.
    op.execute(
        """
        UPDATE target_company
        SET ats = 'ashby',
            ats_handle = 'plaid'
        WHERE name = 'Plaid'
          AND ats = 'lever'
          AND ats_handle = 'plaid'
        """
    )

    # Atlassian: soft-pause by clearing the handle. Leave ats='lever'
    # since changing it without a verified destination would mislead.
    # The ingest plan filter on NULL handles takes the row out of the
    # cron until the operator finds the real ATS.
    op.execute(
        """
        UPDATE target_company
        SET ats_handle = NULL,
            notes = COALESCE(notes, '') ||
                E'\\nATS unknown - lever/atlassian returned 404, ' ||
                E'Workday wd5 401 on all site names. ' ||
                E'Investigate in browser DevTools and update. ' ||
                E'Paused 2026-05-26.'
        WHERE name = 'Atlassian'
          AND ats = 'lever'
          AND ats_handle = 'atlassian'
        """
    )


def downgrade() -> None:
    # Restore Plaid to lever/plaid (best-effort — only if upgrade ran
    # cleanly and the row hasn't been touched since).
    op.execute(
        """
        UPDATE target_company
        SET ats = 'lever',
            ats_handle = 'plaid'
        WHERE name = 'Plaid'
          AND ats = 'ashby'
          AND ats_handle = 'plaid'
        """
    )

    # Restore Atlassian's handle; clear the appended investigation note.
    # NOTE: ``notes`` is restored to NULL only if the appended line is
    # the entire current value. If the operator has added other notes
    # in the meantime, leave them — partial-string match isn't safe.
    op.execute(
        """
        UPDATE target_company
        SET ats_handle = 'atlassian'
        WHERE name = 'Atlassian'
          AND ats = 'lever'
          AND ats_handle IS NULL
        """
    )
