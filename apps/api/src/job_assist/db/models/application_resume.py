"""ApplicationResume ORM model (feat/application-resume, Phase 1).

One tailored resume per application, keyed on ``job_posting_id`` (UNIQUE) —
the resume is an ATTRIBUTE OF THIS APPLICATION, not a library item picked
from a shared pool. This replaces the global ``resume_version`` table + the
apply-time dropdown (which grew 1:1 with applications); those stay dormant
through the migration for rollback safety.

Holds the uploaded ``.docx``/``.pdf`` blob (primary) plus optional pasted/
extracted ``resume_text`` and ``angle``/``label`` for later content↔outcome
analysis (Phase 2 re-points ``resume_analytics`` here). Small single-operator
files → the blob lives in Postgres (LargeBinary/BYTEA); no external storage.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base


class ApplicationResume(Base):
    """The resume the operator sent for one specific application (posting)."""

    __tablename__ = "application_resume"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # One resume per application — UNIQUE so an upload/paste UPSERTS the
    # role's resume rather than stacking rows. CASCADE: if the posting is
    # hard-deleted, its resume goes too.
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Uploaded document (primary). NULL when the operator only pasted text.
    file_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # Paste fallback, or text extracted from the doc (Phase 2) — feeds
    # reuse-search + content-level analysis.
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tailoring thesis — preserves the angle↔outcome analytic dimension.
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free human note. NOT unique (unlike the old resume_version.label).
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
