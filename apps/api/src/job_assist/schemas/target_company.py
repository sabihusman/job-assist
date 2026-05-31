"""Pydantic Read schema for TargetCompany."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import ATS


class TargetCompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    ats: ATS
    ats_handle: str | None
    # Nullable since broad-ingestion (Slice 2): broad-discovered shells
    # have no pedigree tier; it's derived from fit_score at display time.
    tier: int | None
    role_filter: str | None
    domain: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
