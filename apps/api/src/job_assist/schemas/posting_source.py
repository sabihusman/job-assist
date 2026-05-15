"""Pydantic Read schema for PostingSource."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import ATS, FetchStatus


class PostingSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_posting_id: uuid.UUID
    ats: ATS
    source_job_id: str
    source_url: str
    apply_url: str | None
    raw_payload: dict[str, Any]
    parser_version: str
    fetch_status: FetchStatus
    fetched_at: datetime
