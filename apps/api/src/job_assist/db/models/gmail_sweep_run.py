"""GmailSweepRun ORM model (feat/gmail-health-check).

Audit log for each Gmail sweep (poll / backfill) so the health monitor can report
whether Gmail ingestion is still running and how long the last sweep took. The
Gmail poll otherwise keeps no run state — its watermark is derived from
``MAX(outcome_event.received_at)`` on every call — so there was nowhere to read
"did the sweep run, and for how long" until this table.

One row per sweep invocation. Written from an ISOLATED session (see
``services/gmail_sweep_run.record_sweep``) so the timing record survives even when
the sweep's own transaction rolls back on failure.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base


class GmailSweepRun(Base):
    """One Gmail sweep (poll or backfill) — start, finish, status, counts."""

    __tablename__ = "gmail_sweep_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 'poll' (the 6-hourly cron) or 'backfill' (manual wide-window pull). TEXT
    # with a CHECK guard rather than a PG enum — same rationale as posting_action.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="running", server_default="running"
    )
    messages_listed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    outcomes_inserted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('poll','backfill')", name="ck_gmail_sweep_run_kind"),
        CheckConstraint(
            "status IN ('running','success','failed')", name="ck_gmail_sweep_run_status"
        ),
        # The health endpoint reads the single most-recent row by started_at.
        Index("idx_gmail_sweep_run_started_at", "started_at"),
    )
