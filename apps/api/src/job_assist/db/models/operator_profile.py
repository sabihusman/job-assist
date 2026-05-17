"""OperatorProfile ORM model — singleton row at id=1.

Captures operator-tunable parameters that today are hardcoded in
``triage/config.py:HardRuleConfig``. PR #26 ships storage + API only;
the hard-rule consumer still reads its own defaults. PR #29+ rewires
consumers to read from this table.

The singleton constraint (``CHECK (id = 1)``) means we never need to
worry about which row to read — there is exactly one. The seed is
inserted by the migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base


class OperatorProfile(Base):
    """Singleton operator-tunable parameters. There is exactly one row, id=1."""

    __tablename__ = "operator_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # Free-form description of what the operator is currently looking for.
    # Will be used as part of the embedding-similarity profile in PR #29+.
    looking_for_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # JSONB list[str]. Mapped[list[str]] mirrors the SQLAlchemy 2.0 typing
    # pattern; asyncpg returns Python lists for JSONB columns directly.
    role_keywords: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Mirrors HardRuleConfig.geo_whitelist. Seeded with the current defaults.
    geo_whitelist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Mirrors HardRuleConfig.salary_floor_usd (USD/year).
    salary_floor_usd: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("85000")
    )

    # Mirrors HardRuleConfig.applicant_cap.
    applicant_cap: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("150"))

    # Mirrors HardRuleConfig.staffing_firm_blocklist.
    staffing_firm_blocklist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
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

    __table_args__ = (CheckConstraint("id = 1", name="ck_operator_profile_singleton"),)
