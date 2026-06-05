"""ApplicationState ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base

# feat/manual-application-status Phase 1: the manual lifecycle vocabulary.
# Stored as TEXT + CHECK (mirroring ``posting_action.action_type``) rather than
# a PG enum, so the vocabulary can grow without the ALTER TYPE ADD VALUE trap.
# A row exists ONLY once the operator sets one of these; triage state lives in
# ``posting_action``, not here. Order is the lifecycle order.
APPLICATION_STATUS_VALUES: tuple[str, ...] = (
    "applied",
    "interview",
    "offer",
    "accepted",
    "rejected",
)
# Terminal stages drop a card out of the Applied tab (resolved-status logic in
# services/postings_query.py). ``accepted`` and ``rejected`` are end-states.
APPLICATION_STATUS_TERMINAL: frozenset[str] = frozenset({"accepted", "rejected"})


class ApplicationState(Base):
    """Operator's decision state for a job posting — one row per posting."""

    __tablename__ = "application_state"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # TEXT + CHECK, not a PG enum — see APPLICATION_STATUS_VALUES above. No
    # default: every row is created with an explicit lifecycle status via
    # PUT /postings/{id}/status.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(
            "status IN ('applied','interview','offer','accepted','rejected')",
            name="ck_application_state_status",
        ),
        Index("idx_application_state_status", "status"),
        Index(
            "idx_application_state_snooze_until",
            "snooze_until",
            postgresql_where=text("snooze_until IS NOT NULL"),
        ),
    )
