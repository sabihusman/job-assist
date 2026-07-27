"""Pydantic schemas for the /admin/reclassify endpoints.

D-ASYNC-RESWEEP: the sweep moved to a job-row + poll model. POST inserts a
``reclassify_job`` row and returns 202 with ``ReclassifySweepAccepted``;
progress/results are polled via GET /admin/reclassify/jobs/{job_id}
(``ReclassifyJobStatus``). The old synchronous ``ReclassifySweepResponse``
(processed/changed/skipped/distribution, PR #48) is gone with the blocking
execution model that produced it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReclassifySweepRequest(BaseModel):
    """Body for POST /admin/reclassify/sweep."""

    limit: int = Field(
        default=50,
        ge=1,
        le=5000,
        description=(
            "Maximum number of postings this job may process (its budget). "
            "The old 500 cap guarded the synchronous request's HTTP timeout "
            "window; the job now runs server-side in batches, so the cap only "
            "bounds the worst-case Gemini spend of a single job. 5000 covers "
            "a full-backlog resweep with headroom."
        ),
    )
    only_unclassified: bool = Field(
        default=True,
        description=(
            "When True (default), only touch postings where role_family='other' "
            "OR seniority_level='unknown' (the buckets the regex assigns when it "
            "can't determine a value). "
            "When False, reclassify ALL postings regardless of current values — "
            "useful for a full re-sweep after a prompt upgrade."
        ),
    )


class ReclassifySweepAccepted(BaseModel):
    """202 response for POST /admin/reclassify/sweep — poll the job for progress."""

    job_id: uuid.UUID = Field(
        description="reclassify_job row id — poll GET /admin/reclassify/jobs/{job_id}."
    )
    status: str = Field(description="Initial job status; always 'queued' at accept time.")


class ReclassifyJobStatus(BaseModel):
    """Response for GET /admin/reclassify/jobs/{job_id} — the job row as JSON."""

    job_id: uuid.UUID
    status: str = Field(description="queued | running | succeeded | failed.")
    requested_limit: int = Field(description="The job's processing budget (request's `limit`).")
    processed: int = Field(description="Rows attempted so far (updated after every batch).")
    changed: int = Field(description="Rows where at least one field changed so far.")
    error: str | None = Field(default=None, description="Set when status='failed'.")
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
