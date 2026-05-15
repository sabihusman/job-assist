"""Pydantic Read schema for ClosedChannel."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import ClosedChannelReason


class ClosedChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_company_id: uuid.UUID | None
    company_name: str
    reason: ClosedChannelReason
    rejection_count: int
    notes: str | None
    closed_at: datetime
    unsealed_at: datetime | None
