"""Wire shapes for the async Gmail backfill (D-SETTINGS-REWIRE Part C).

Job-row + poll pattern, same shape as ``schemas/reclassify.py``. The job row
is the EXISTING ``gmail_sweep_run`` audit row (kind='backfill') — the sync
endpoint already wrote one row per backfill, so reusing it as the job row
keeps a single source of truth for sweep state and needs no new table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GmailBackfillAccepted(BaseModel):
    """202 response for POST /admin/gmail/backfill — poll the job for progress."""

    job_id: uuid.UUID = Field(
        description="gmail_sweep_run row id — poll GET /admin/gmail/jobs/{job_id}."
    )
    status: str = Field(description="Initial job status; always 'running' at accept time.")
    days_back: int = Field(description="The window width the job was queued with.")


class GmailSweepJobStatus(BaseModel):
    """Response for GET /admin/gmail/jobs/{job_id} — the sweep row as JSON.

    Serves any ``gmail_sweep_run`` row (the 6-hourly poll cron's rows
    included); ``kind`` disambiguates.
    """

    job_id: uuid.UUID
    kind: str = Field(description="'backfill' (manual wide-window pull) or 'poll' (cron).")
    status: str = Field(description="running | success | failed.")
    started_at: datetime
    finished_at: datetime | None = None
    messages_listed: int = Field(description="Gmail messages the sweep listed.")
    outcomes_inserted: int = Field(description="outcome_event rows the sweep inserted.")
    error_message: str | None = Field(default=None, description="Set when status='failed'.")
