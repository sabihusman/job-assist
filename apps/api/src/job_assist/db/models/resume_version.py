"""ResumeVersion ORM model (feat/resume-version-tracking).

The operator tailors a resume per role (e.g. a Betterment trust/compliance
variant). A ``resume_version`` row registers one such variant so an
application (``posting_action`` with ``action_type='applied'``) can tag
which resume was sent — enabling later correlation of resume angle
against outcomes (rejection patterns by version).

Storage choice: label + angle + optional plain-text snapshot + notes —
NOT a binary file. The analytic goal is correlating *content/angle* with
outcomes, which wants queryable text, not an opaque blob. ``snapshot_text``
is nullable so the operator can paste the variant's text for content-level
analysis or skip it and rely on ``angle``/``label`` for coarse tracking.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base


class ResumeVersion(Base):
    """One tailored resume variant the operator can tag onto an application."""

    __tablename__ = "resume_version"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Human-referenceable handle, e.g. "betterment-trust-v1". UNIQUE so
    # tagging is idempotent and the operator can name a version once.
    label: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # The tailoring thesis — "lead with trust/compliance + fintech depth".
    # Queryable for coarse angle-vs-outcome analysis.
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional plain-text dump of the variant for content-level
    # correlation (keyword/section presence). NULL = label/angle only.
    snapshot_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
