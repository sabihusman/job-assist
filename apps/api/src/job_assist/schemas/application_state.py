"""Pydantic Read schema for ApplicationState."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import ApplicationStatus


class ApplicationStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_posting_id: uuid.UUID
    status: ApplicationStatus
    applied_at: datetime | None
    snooze_until: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
