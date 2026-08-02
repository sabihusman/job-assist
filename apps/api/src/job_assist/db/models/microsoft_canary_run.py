"""MicrosoftCanaryRun ORM model (directive B, Phase 3).

Audit log for the Microsoft Careers adapter's dedicated health canary — ONE
lightweight live call (the "product manager" search page) per health cycle,
kept deliberately separate from the real multi-query paginated ingest run
(``adapters/microsoft.py::MicrosoftCareersAdapter.fetch_postings``) so the
canary stays cheap and fast regardless of how the real ingest is throttled.

Mirrors ``GmailSweepRun`` — the only other place this codebase persists a
dedicated run-outcome row purely for the health monitor to read.

``status`` distinguishes the three failure modes the directive calls out;
the health endpoint collapses them to one bool for the dot but shows the
distinct mode in the human-readable message/popover:
  * ``ok``               — 200, schema valid, at least one keep on the page.
  * ``endpoint_failure``  — request raised, or a non-200 status.
  * ``schema_drift``      — 200, but the response doesn't match the pinned
                             shape (``data.positions`` list of {id, name}).
  * ``zero_results``      — 200, schema valid, but zero title-filter keeps on
                             a query that normally returns many.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base


class MicrosoftCanaryRun(Base):
    """One Microsoft Careers canary check — start, finish, status, detail."""

    __tablename__ = "microsoft_canary_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok','endpoint_failure','schema_drift','zero_results')",
            name="ck_microsoft_canary_run_status",
        ),
        # The health endpoint reads the single most-recent row by started_at.
        Index("idx_microsoft_canary_run_started_at", "started_at"),
    )
