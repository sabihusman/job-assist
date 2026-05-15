"""OutcomeEvent ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from job_assist.db.base import Base
from job_assist.db.enums import OutcomeType


class OutcomeEvent(Base):
    """A classified Gmail message representing an application outcome.

    Linked to a JobPosting where known; falls back to target_company when the
    specific posting cannot be identified.
    """

    __tablename__ = "outcome_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_company.id", ondelete="SET NULL"),
        nullable=True,
    )
    email_message_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    email_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    from_address: Mapped[str] = mapped_column(String, nullable=False)
    from_domain: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome_type: Mapped[OutcomeType] = mapped_column(
        SAEnum(OutcomeType, name="outcome_type", create_type=False),
        nullable=False,
        default=OutcomeType.unclassified,
        server_default=OutcomeType.unclassified.value,
    )
    classifier_version: Mapped[str] = mapped_column(String, nullable=False)
    classifier_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("email_message_id", name="uq_outcome_event_email_message_id"),
        Index("idx_outcome_event_target_company_id", "target_company_id"),
        Index("idx_outcome_event_job_posting_id", "job_posting_id"),
        Index("idx_outcome_event_outcome_type", "outcome_type"),
        Index("idx_outcome_event_received_at", "received_at"),
    )
