"""DiscoveredHandle ORM model (Slice 2 of broad-ingestion expansion).

A handle is an ``(ats, handle)`` pair discovered for broad ingestion —
distinct from ``target_company.ats_handle`` (the curated 30, hand-picked
and richly annotated). Discovered handles are the long-tail: hundreds-
to-thousands of company job boards we sweep with the title pre-filter
to surface PM roles regardless of company pedigree.

Slice 2 (this PR) hand-seeds ~50 known-good handles for a bounded trial.
Slice 3 will feed this table from a Common Crawl CDX scan and add the
weekly qualified-row cap.

Lifecycle columns let the broad-ingest runner deregister stale handles:
  * ``last_ingested_at`` — when the runner last pulled this handle.
  * ``consecutive_empty_count`` — bumped each run that returns zero
    postings; reset to 0 on any non-empty pull. The runner flips
    ``active=False`` once it crosses a threshold (Slice 3), so a board
    that 404s or empties out stops wasting API calls.
  * ``active`` — the runner only pulls ``active=True`` rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from job_assist.db.base import Base


class DiscoveredHandle(Base):
    """An ``(ats, handle)`` pair sourced for broad ingestion."""

    __tablename__ = "discovered_handle"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # ATS enum value as plain text (greenhouse|lever|ashby). Not a PG
    # enum: the broad set may eventually include ATSes we don't have a
    # SAEnum member for yet, and a plain string keeps seeding flexible.
    ats: Mapped[str] = mapped_column(String, nullable=False)
    handle: Mapped[str] = mapped_column(String, nullable=False)
    # Provenance — 'hand_seed_trial' for Slice 2; 'cdx' / 'domain_probe'
    # for Slice 3. Lets a future cleanup distinguish trial rows from
    # scanned rows.
    source: Mapped[str] = mapped_column(
        String, nullable=False, default="hand_seed_trial", server_default="hand_seed_trial"
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_empty_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        # Idempotent seeding: one row per (ats, handle). Re-running the
        # seed script upserts rather than duplicating.
        Index("uq_discovered_handle_ats_handle", "ats", "handle", unique=True),
        # The runner's hot query is ``WHERE active = true`` — partial
        # index keeps it cheap as the table grows to thousands of rows.
        Index(
            "idx_discovered_handle_active",
            "active",
            postgresql_where=text("active = true"),
        ),
    )
