"""Pydantic models for the Gmail backfill pipeline.

Three layers:
  * ``RawEmail`` — what comes out of the Gmail API after header/body parsing.
  * ``ClassificationResult`` — what comes back from Gemini Flash Lite.
  * ``BackfillReport`` — counters returned by the orchestrator and exposed
    on the admin endpoint.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawEmail(BaseModel):
    """A single Gmail message after header/body parsing.

    Body fields are best-effort: the Gmail API returns a MIME tree and we
    extract the plain-text part first, falling back to a stripped HTML part
    if no plain alternative is present. ``snippet`` is what Gmail itself
    pre-computes (≈ first 200 chars) — useful as a low-cost fallback.
    """

    message_id: str
    thread_id: str | None = None
    from_address: str
    from_name: str | None = None
    from_domain: str
    to_addresses: list[str] = Field(default_factory=list)
    subject: str
    received_at: datetime
    body_text: str = ""
    body_html: str = ""
    snippet: str = ""
    labels: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    """Structured output from the Gemini classifier."""

    outcome_type: str  # one of OutcomeType enum values
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_company: str | None = None
    reasoning: str = ""


class BackfillReport(BaseModel):
    """Counters returned by ``run_backfill`` for the admin endpoint."""

    days_back: int
    window_start: datetime
    window_end: datetime
    message_ids_listed: int = 0
    fetched: int = 0
    skipped_prefilter: int = 0
    skipped_already_classified: int = 0
    classified_job_related: int = 0
    classified_unrelated: int = 0
    classifier_errors: int = 0
    fetch_errors: int = 0
    outcome_events_inserted: int = 0
    target_company_links: int = 0
