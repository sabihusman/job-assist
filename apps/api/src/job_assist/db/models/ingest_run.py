"""IngestRun ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base
from job_assist.db.enums import ATS, IngestRunStatus


class IngestRun(Base):
    """Audit log for each ingestion run — one row per (source, invocation)."""

    __tablename__ = "ingest_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[ATS] = mapped_column(
        SAEnum(ATS, name="ats_type", create_type=False),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[IngestRunStatus] = mapped_column(
        SAEnum(IngestRunStatus, name="ingest_run_status", create_type=False),
        nullable=False,
        default=IngestRunStatus.running,
        server_default=IngestRunStatus.running.value,
    )
    postings_fetched: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    postings_new: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    postings_updated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_ingest_run_started_at", "started_at"),
        Index("idx_ingest_run_source_started_at", "source", "started_at"),
    )
