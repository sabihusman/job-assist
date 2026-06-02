"""JobPosting ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base
from job_assist.db.enums import RemoteType, RoleFamily, SalaryPeriod, SeniorityLevel


class JobPosting(Base):
    """A single job posting, deduplicated and normalised across ATS sources."""

    __tablename__ = "job_posting"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_company_name: Mapped[str] = mapped_column(String, nullable=False)
    target_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_company.id", ondelete="SET NULL"),
        nullable=True,
    )
    normalized_title: Mapped[str] = mapped_column(String, nullable=False)
    raw_title: Mapped[str] = mapped_column(String, nullable=False)
    location_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    locations_normalized: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    remote_type: Mapped[RemoteType] = mapped_column(
        SAEnum(RemoteType, name="remote_type", create_type=False),
        nullable=False,
        default=RemoteType.unknown,
        server_default=RemoteType.unknown.value,
    )
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    salary_period: Mapped[SalaryPeriod] = mapped_column(
        SAEnum(SalaryPeriod, name="salary_period", create_type=False),
        nullable=False,
        default=SalaryPeriod.unknown,
        server_default=SalaryPeriod.unknown.value,
    )
    seniority_level: Mapped[SeniorityLevel] = mapped_column(
        SAEnum(SeniorityLevel, name="seniority_level", create_type=False),
        nullable=False,
        default=SeniorityLevel.unknown,
        server_default=SeniorityLevel.unknown.value,
    )
    role_family: Mapped[RoleFamily] = mapped_column(
        SAEnum(RoleFamily, name="role_family", create_type=False),
        nullable=False,
        default=RoleFamily.other,
        server_default=RoleFamily.other.value,
    )
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    jd_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    should_embed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Public-applicant count from ATS where exposed (LinkedIn-style). NULL is
    # the dominant state today — no Greenhouse/Lever/Ashby endpoint surfaces
    # it. The hard-rule filter tolerates NULL by skipping the cap check.
    applicant_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Department / team strings extracted by each adapter from its native
    # payload shape (Greenhouse: departments[0].name; Lever / Ashby:
    # categories.department + categories.team). Both nullable — most
    # postings have one, the other, or neither. Indexed (partial) for the
    # division-discovery query in PR #28b.
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    team: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── JD summary enrichment (PR #41) ───────────────────────────────────
    # Gemini-generated markdown summary, set once and treated as cached
    # until /retry is called. Same six-status state machine as company /
    # division enrichment; the sweep skips rows where
    # ``jd_summary_markdown IS NOT NULL``.
    jd_summary_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    jd_summary_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    jd_summary_enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    jd_summary_enrichment_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # ── LLM classifier metadata (PR #48) ─────────────────────────────────
    # Stamped by /admin/reclassify/sweep. NULL on rows that have only ever
    # been classified by the ingest-time regex heuristic (normalization.py).
    # Non-null values mean the LLM sweep has run at least once.
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classifier_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Heuristic fit score (PR #56) ─────────────────────────────────────
    # 0-100 composite score from ``services/scoring.py``. Written by both
    # the ingest path (in IngestionService) and the classifier sweep
    # (re-scored after each successful classification) AND by the
    # standalone POST /admin/score/sweep endpoint for backfill.
    # ``scorer_version`` lets us detect rows scored by an older heuristic
    # and rescore them when the algorithm changes.
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scorer_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Hard-rule eligibility (PR C) ─────────────────────────────────────
    # ``hard_rule_failed`` stores the RuleName that failed (e.g. "salary_floor")
    # or NULL when the posting passed every rule. Written at ingest and by
    # POST /admin/postings/reeval-hard-rules. ``GET /postings`` filters
    # ``hard_rule_failed IS NULL`` by default (index-backed, partial). NULL
    # also means "not yet evaluated" — pre-backfill rows read as passing,
    # which is the safe default (surface rather than hide).
    hard_rule_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_rules_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── Semantic embedding (slice 1, feat/embeddings-slice1) ─────────────
    # text-embedding-004 vector of the JD (summary if present, else
    # jd_text[:3000]). Populated by the opt-in POST /admin/embeddings/sweep
    # — NOT at ingest, so it never auto-costs. NULL = not yet embedded.
    # NOTHING reads this for ranking in slice 1: fit_score / score_posting
    # are untouched. ``jd_text_hash_embedded`` snapshots the jd_text_hash at
    # embed time so the sweep re-embeds only when the JD text changed.
    # ``embedded_source`` records which text was embedded ("summary" |
    # "jd_text") for debuggability. The enrichment-style attempt counter +
    # error column mirror the jd-summary state machine.
    jd_embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    jd_text_hash_embedded: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="idx_job_posting_content_hash"),
        Index("idx_job_posting_jd_text_hash", "jd_text_hash"),
        Index("idx_job_posting_first_seen_at", "first_seen_at"),
        Index("idx_job_posting_target_company_id", "target_company_id"),
        Index(
            "idx_job_posting_should_embed",
            "first_seen_at",
            postgresql_where=text("should_embed = true"),
        ),
        Index(
            "ix_job_posting_target_company_department",
            "target_company_id",
            "department",
            postgresql_where=text("department IS NOT NULL"),
        ),
        Index(
            "ix_job_posting_target_company_team",
            "target_company_id",
            "team",
            postgresql_where=text("team IS NOT NULL"),
        ),
    )
