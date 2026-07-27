"""ReclassifyJob ORM model (directive D-ASYNC-RESWEEP).

One row per POST /admin/reclassify/sweep — the job-row half of the job-row +
poll pattern that makes the sweep non-blocking. The endpoint inserts the row
(status='queued') and returns 202 immediately; the in-process worker
(``services/reclassify_sweep.run_reclassify_job``) flips it to 'running',
bumps ``processed``/``changed``/``updated_at`` after every server-side batch,
and finalizes to 'succeeded' or 'failed' (+ ``error``, ``finished_at``).
GET /admin/reclassify/jobs/{id} returns the row as JSON.

``status`` is TEXT with a CHECK guard rather than a PG enum — same rationale
as ``gmail_sweep_run`` (the other run-status audit table).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base


class ReclassifyJob(Base):
    """One async reclassify sweep — status, counters, timestamps."""

    __tablename__ = "reclassify_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default="queued"
    )
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    changed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_reclassify_job_status",
        ),
        Index("idx_reclassify_job_created_at", "created_at"),
    )
