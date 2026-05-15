"""Pydantic Read schema for IngestRun."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import ATS, IngestRunStatus


class IngestRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: ATS
    started_at: datetime
    finished_at: datetime | None
    status: IngestRunStatus
    postings_fetched: int
    postings_new: int
    postings_updated: int
    error_message: str | None
    error_traceback: str | None
