"""TargetCompany ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # Per-ATS extension config — needed by Workday for wd_number + site
    # (PR #33); NULL for Greenhouse / Lever / Ashby where the public
    # endpoints don't need extras.
    adapter_config: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    role_filter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Enrichment (PR #27) ─────────────────────────────────────────────────
    # One-sentence description from Gemini Flash Lite. Set once and treated
    # as cached forever; the sweep skips rows where this is NOT NULL.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last enrichment error string (NULL on success). Capped at ~500 chars
    # in the service layer so a long stack trace doesn't blow up the row.
    enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Failed attempts since last success. The sweep refuses to retry after
    # ``settings.company_enrich_max_attempts`` until a manual /retry call
    # resets it to 0.
    enrichment_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
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
