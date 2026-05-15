"""Pydantic Read schema for TriageResult."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TriageResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_posting_id: uuid.UUID
    score: float
    verdict_text: str | None
    rule_flags: dict[str, Any] | None
    features: dict[str, Any] | None
    profile_version: str
    model_version: str | None
    created_at: datetime
