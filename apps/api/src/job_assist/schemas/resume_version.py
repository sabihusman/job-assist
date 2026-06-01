"""Pydantic schemas for resume-version tracking (feat/resume-version-tracking)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class ResumeVersionCreate(BaseModel):
    """Body for ``POST /admin/resume-versions``."""

    label: str
    angle: str | None = None
    snapshot_text: str | None = None
    notes: str | None = None

    @field_validator("label")
    @classmethod
    def _label_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label must be non-empty")
        return v


class ResumeVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    angle: str | None
    snapshot_text: str | None
    notes: str | None
    created_at: datetime
