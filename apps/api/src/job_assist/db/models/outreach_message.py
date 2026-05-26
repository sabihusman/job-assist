"""OutreachMessage ORM model (PR #52).

Append-only event log of operator outreach. Mirrors the
``posting_action`` design from PR #31: TEXT columns guarded by
CHECK constraints, latest-row reads computed via LATERAL on
the (contact_id, sent_at DESC) index.

Vocabulary lives in TEXT (no PG enum) to evolve without ALTER TYPE.

Sources:
* ``manual``     — operator logs from the Contacts page (PR #52).
* ``gmail_auto`` — Gmail correspondence auto-detection (PR #53).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base


class OutreachMessage(Base):
    """One outreach message to/from a contact. Append-only.

    Current state for contact C = the row with the largest ``sent_at``
    where ``contact_id = C``.
    ``idx_outreach_message_contact_id_sent_at_desc`` makes that
    lookup index-only.
    """

    __tablename__ = "outreach_message"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contact.id", ondelete="CASCADE"),
        nullable=False,
    )
    # TEXT, not a PG enum — see module docstring.
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # FK to job_posting (ON DELETE SET NULL — preserve history if the
    # posting is later deleted).
    posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # Gmail Message-ID / LinkedIn thread ID. NULL for manual rows;
    # populated by PR #53. Partial UNIQUE index in the migration so
    # PR #53's upserts can't insert dupes.
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SQLAlchemy reserves ``metadata`` on the Base class, so name the
    # Python attribute ``message_metadata`` and pin the DB column to
    # the brief's ``metadata`` via ``name=``.
    message_metadata: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "direction IN ('outbound','inbound')",
            name="ck_outreach_message_direction",
        ),
        CheckConstraint(
            "channel IN ('email','linkedin','other')",
            name="ck_outreach_message_channel",
        ),
        CheckConstraint(
            "source IN ('manual','gmail_auto')",
            name="ck_outreach_message_source",
        ),
        # Latest-message-per-contact lookups land here. The DESC ordering
        # is declared in the migration via raw SQL; this Index entry is
        # for model-aware tooling only.
        Index(
            "idx_outreach_message_contact_id_sent_at_desc",
            "contact_id",
            "sent_at",
        ),
    )
