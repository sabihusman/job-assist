"""ClosedChannel ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base
from job_assist.db.enums import ClosedChannelReason


class ClosedChannel(Base):
    """Companies where the operator has decided to stop pursuing opportunities.

    A company is "sealed" when unsealed_at IS NULL.  The operator can
    unseal a company by setting unsealed_at, which removes it from the
    hard-rule filter without deleting the history.
    """

    __tablename__ = "closed_channel"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_company.id", ondelete="SET NULL"),
        nullable=True,
    )
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[ClosedChannelReason] = mapped_column(
        SAEnum(ClosedChannelReason, name="closed_channel_reason", create_type=False),
        nullable=False,
        default=ClosedChannelReason.other,
        server_default=ClosedChannelReason.other.value,
    )
    rejection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    unsealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Partial unique index: only one active (unsealed) record per company name.
        # UniqueConstraint doesn't support postgresql_where; use Index with unique=True.
        Index(
            "uq_closed_channel_company_name_active",
            "company_name",
            unique=True,
            postgresql_where=text("unsealed_at IS NULL"),
        ),
        Index("idx_closed_channel_company_name", "company_name"),
    )
