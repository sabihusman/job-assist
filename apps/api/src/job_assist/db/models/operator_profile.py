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

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
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

    # PR #43: optional upper bound paired with the floor. NULL = no ceiling
    # (hard-rule filter skips the check). When set, postings whose declared
    # salary_min exceeds this value are dropped.
    salary_ceiling_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Mirrors HardRuleConfig.applicant_cap. Default raised 150 → 500
    # in May 2026 — see ``DECISIONS.md`` ADR-008 history note. The
    # existing seeded singleton row was migrated by the same change.
    applicant_cap: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("500"))

    # Per-company surfacing cap (feat/tunable-per-company-cap). How many of
    # each company's top-by-fit_score postings the list/export views surface;
    # 0 = disabled (show all). Always enforced server-side
    # (postings_query ROW_NUMBER CTE); this column is the persisted operator
    # default the list/count/export endpoints fall back to when no explicit
    # ``?per_company_cap`` override is supplied. Default 3 matches the prior
    # hardcoded endpoint default, so behaviour is unchanged until the operator
    # moves it.
    per_company_cap: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))

    # PR #43: explicit list of ``SeniorityLevel`` enum values to include.
    # NULL or empty = include all levels (filter disabled). A posting with
    # ``seniority_level`` NOT in this set is dropped; postings with NULL /
    # ``unknown`` seniority pass through (we surface for triage rather than
    # silently drop on missing data).
    seniority_levels_included: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Mirrors HardRuleConfig.staffing_firm_blocklist.
    staffing_firm_blocklist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # ── Semantic profile embedding (slice 1, feat/embeddings-slice1) ─────
    # gemini-embedding-001 (768-dim) vector of ``looking_for_text``. Re-embedded on
    # PUT /operator/profile when the text changes (hash-gated). NULL until
    # the operator sets a non-empty ``looking_for_text`` and it embeds.
    # NOTHING reads this for ranking in slice 1 — it powers only the
    # read-only GET /admin/embeddings/nearest validation gate.
    looking_for_embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    looking_for_embedding_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    looking_for_embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
