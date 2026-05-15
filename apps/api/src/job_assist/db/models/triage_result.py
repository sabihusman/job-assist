"""TriageResult ORM model (skeleton; verdict_text and features populated Week 3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base


class TriageResult(Base):
    """Triage score and verdict for a job posting — one row per posting."""

    __tablename__ = "triage_result"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    verdict_text: Mapped[str | None] = mapped_column(String, nullable=True)
    rule_flags: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    features: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    profile_version: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_triage_result_score", "score"),
        Index("idx_triage_result_created_at", "created_at"),
    )
