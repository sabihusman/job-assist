"""PostingSource ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base
from job_assist.db.enums import ATS, FetchStatus


class PostingSource(Base):
    """Raw ATS record for a job posting — one row per (ats, source_job_id) pair.

    Multiple sources can map to the same JobPosting after deduplication.
    """

    __tablename__ = "posting_source"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="CASCADE"),
        nullable=False,
    )
    ats: Mapped[ATS] = mapped_column(
        SAEnum(ATS, name="ats_type", create_type=False),
        nullable=False,
    )
    source_job_id: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    fetch_status: Mapped[FetchStatus] = mapped_column(
        SAEnum(FetchStatus, name="fetch_status", create_type=False),
        nullable=False,
        default=FetchStatus.ok,
        server_default=FetchStatus.ok.value,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("ats", "source_job_id", name="uq_posting_source_ats_job_id"),
        Index("idx_posting_source_job_posting_id", "job_posting_id"),
    )
