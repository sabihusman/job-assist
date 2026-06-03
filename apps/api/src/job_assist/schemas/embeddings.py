"""Response schemas for the embedding sweep + nearest-neighbour endpoints
(slice 1, feat/embeddings-slice1).

Typed so OpenAPI documents the validation gate's shape. No request schemas —
both endpoints take only query params (``limit`` / ``n``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EmbeddingSweepResponse(BaseModel):
    """Counters from ``POST /admin/embeddings/sweep``."""

    total: int
    embedded: int
    skipped: int
    exhausted: int
    missing_context: int
    errors: int
    error_details: list[dict[str, str]]


class EmbeddingRetryResponse(BaseModel):
    """Result of ``POST /admin/embeddings/{posting_id}/retry``."""

    status: str
    posting_id: str | None = None
    source: str | None = None
    error: str | None = None


class NearestPosting(BaseModel):
    """One row of the nearest-neighbour validation result."""

    posting_id: str
    title: str
    company: str
    cosine_sim: float
    fit_score: int | None
    # slice 2a: calibrated 0-100 similarity (PERCENT_RANK of cosine-to-profile).
    # NULL until POST /admin/embeddings/recalibrate has run. Declared here so the
    # response_model doesn't strip it off the GET (the slice-1 model omitted it).
    similarity_score: int | None = None
    embedded_source: str | None


class NearestResponse(BaseModel):
    """``GET /admin/embeddings/nearest`` — the slice-1 go/no-go view.

    ``available`` is False (with a ``reason``) when the profile or corpus
    isn't embedded yet. ``spread`` carries the cosine min/median/max across all
    embedded open rows when available.
    """

    available: bool
    reason: str | None = None
    n: int | None = None
    results: list[NearestPosting] = []
    spread: dict[str, float | int] | None = None
    # slice 2a: distribution of the calibrated similarity_score (count /
    # calibrated_count / min / p25 / median / p75 / max) — the verification gate.
    # dict[str, Any] because the not-yet-calibrated case carries a string note.
    # Declared so the response_model surfaces it (the slice-1 model omitted it,
    # which silently stripped it off the GET).
    similarity_spread: dict[str, Any] | None = None
