"""Division ORM model — discovered from job_posting.department/team tuples.

Each row represents a department/team combination that has surfaced in
at least one ``job_posting`` for a given ``target_company``. Cascade-deleted
when the parent target_company is removed (operator never wants stale
division descriptions for a company they've cut).

The ``UNIQUE NULLS NOT DISTINCT`` constraint on
(target_company_id, department, team) means two postings with
(``X``, ``Eng``, NULL) collapse to a single division row — without that
flag, SQL's default treatment of NULL as never-equal would let duplicates
accumulate.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base


class Division(Base):
    """A (target_company, department, team) tuple plus its enriched description."""

    __tablename__ = "division"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_company.id", ondelete="CASCADE"),
        nullable=False,
    )
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    team: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Gemini-generated one-sentence description. Cached forever once set —
    # the sweep skips rows where this is NOT NULL.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enrichment_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
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
        # NULLS NOT DISTINCT (PG 15+): treat NULLs as equal for uniqueness so
        # (X, Eng, NULL) collapses to one row across many postings.
        UniqueConstraint(
            "target_company_id",
            "department",
            "team",
            name="uq_division_company_dept_team",
            postgresql_nulls_not_distinct=True,
        ),
        Index("idx_division_target_company_id", "target_company_id"),
    )
