"""Pydantic Read schema for OutcomeEvent."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import OutcomeType


class OutcomeEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_posting_id: uuid.UUID | None
    target_company_id: uuid.UUID | None
    email_message_id: str
    email_thread_id: str | None
    from_address: str
    from_domain: str
    subject: str
    received_at: datetime
    outcome_type: OutcomeType
    classifier_version: str
    classifier_confidence: float | None
    raw_snippet: str | None
