"""Pydantic Read schema for JobPosting."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from job_assist.db.enums import RemoteType, RoleFamily, SalaryPeriod, SeniorityLevel


class JobPostingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    canonical_company_name: str
    target_company_id: uuid.UUID | None
    normalized_title: str
    raw_title: str
    location_raw: str | None
    locations_normalized: dict[str, Any] | None
    remote_type: RemoteType
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    salary_period: SalaryPeriod
    seniority_level: SeniorityLevel
    role_family: RoleFamily
    jd_text_hash: str
    content_hash: str
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    should_embed: bool
