"""PostingAction ORM model (PR #31).

Append-only event store for operator actions on job postings. The
*current* state for a posting is the latest row by ``created_at`` for
that ``job_posting_id``; ``reset`` is the sentinel "back to triage".

We intentionally do NOT reuse ``outcome_event`` (which stores Gmail-side
events from the world) or ``application_state`` (one-row-per-posting,
predates PR #31's append-only model). Both stay where they are; only
``posting_action`` is wired into the new triage endpoints.

Column-level CHECK constraints encode the validity rules at the DB layer
so the data store stays consistent even if a future caller bypasses the
service-level guard in ``services/posting_actions.py``.

Vocabulary lives in Python TEXT (no PG enum) to avoid an ALTER TYPE
dance every time we evolve the reason list.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from job_assist.db.base import Base


class PostingAction(Base):
    """One operator action on a job posting. Append-only.

    Current state for posting P = the row in ``posting_action`` with the
    largest ``created_at`` where ``job_posting_id = P``.
    ``ix_posting_action_job_posting_id_created_at_desc`` makes that
    lookup index-only.
    """

    __tablename__ = "posting_action"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        default=uuid.uuid4,
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting.id", ondelete="CASCADE"),
        nullable=False,
    )
    # TEXT, not a PG enum — see module docstring.
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # feat/resume-version-tracking: optional tag for which tailored resume
    # variant was sent with this application. Only meaningful on an
    # ``applied`` row (CHECK below). NULL on every other action type and
    # on quick applies the operator didn't tag.
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Action vocabulary guard.
        CheckConstraint(
            "action_type IN ('interested','not_interested','applied','snoozed','reset')",
            name="ck_posting_action_action_type",
        ),
        # A resume tag only makes sense on an apply event.
        CheckConstraint(
            "resume_version_id IS NULL OR action_type = 'applied'",
            name="ck_posting_action_resume_only_for_applied",
        ),
        # Reason vocabulary guard (NULL allowed; specific strings otherwise).
        # PR #43 added ``too_senior`` / ``too_junior``.
        # feat/company-app-awareness added ``too_many_open_apps`` — a reluctant
        # portfolio-management pass, deliberately excluded from calibration's
        # fit-learning aggregates (services/stats.py).
        CheckConstraint(
            "reason IS NULL OR reason IN ("
            "'wrong_role','wrong_location','comp_too_low','wrong_industry',"
            "'wrong_stage','already_rejected_here','just_not_feeling_it',"
            "'too_senior','too_junior','too_many_open_apps')",
            name="ck_posting_action_reason",
        ),
        # Reason ↔ action_type cross-rule:
        #   action_type = 'not_interested'  ⇔  reason IS NOT NULL
        # Encoded as a boolean equality so PG enforces "either both true
        # or both false" without an ELSE branch.
        CheckConstraint(
            "(action_type = 'not_interested') = (reason IS NOT NULL)",
            name="ck_posting_action_reason_required_for_not_interested",
        ),
        # snooze_until is only meaningful for snoozed actions.
        CheckConstraint(
            "snooze_until IS NULL OR action_type = 'snoozed'",
            name="ck_posting_action_snooze_until_only_for_snoozed",
        ),
        # Latest-action-per-posting lookups land here.
        Index(
            "ix_posting_action_job_posting_id_created_at_desc",
            "job_posting_id",
            "created_at",
            postgresql_using="btree",
        ),
    )
