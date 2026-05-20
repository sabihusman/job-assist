"""Contact ORM model — outreach pipeline (PR #39).

Generic person schema. Sources include the Tippie alumni directory,
LinkedIn outreach targets, inbound recruiters, and warm intros — each
distinguished by ``source_type`` and with source-specific extras in
``source_metadata``.

Reachability invariant is enforced at the DB level: every contact must
have at least one of ``email_primary`` or ``linkedin_url``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base


class Contact(Base):
    """A person the operator may reach out to."""

    __tablename__ = "contact"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_primary: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_secondary: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_employer: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_city: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_country: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_metro: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    job_functions_of_interest: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    industries_of_interest: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    contact_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    contact_opt_in_topics: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
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
            "email_primary IS NOT NULL OR linkedin_url IS NOT NULL",
            name="ck_contact_has_channel",
        ),
        CheckConstraint(
            "source_type IN ('tippie_alumni','linkedin_outreach','recruiter_inbound','warm_intro')",
            name="ck_contact_source_type",
        ),
        # The LOWER() partial unique indexes (uq_contact_email_primary,
        # uq_contact_linkedin_url) are created via raw SQL in the
        # migration — SQLAlchemy 2.0 can't natively express a unique
        # index on a function expression *with* postgresql_where.
        # We declare a plain (non-functional) index here so model-aware
        # tooling at least knows the column matters.
        Index("idx_contact_source_type", "source_type"),
    )
