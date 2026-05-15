"""TargetCompany ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base
from job_assist.db.enums import ATS


class TargetCompany(Base):
    """Known companies the operator is actively targeting.

    Seeded in the seed migration; updated by the ATS discovery probe.
    """

    __tablename__ = "target_company"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    ats: Mapped[ATS] = mapped_column(
        SAEnum(ATS, name="ats_type", create_type=False),
        nullable=False,
        default=ATS.unknown,
        server_default=ATS.unknown.value,
    )
    ats_handle: Mapped[str | None] = mapped_column(String, nullable=True)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    role_filter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_target_company_tier", "tier"),
        Index("idx_target_company_ats", "ats"),
    )
