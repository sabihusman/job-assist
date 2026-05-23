"""Pydantic schemas for the /admin/score/sweep endpoint (PR #56)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreSweepRequest(BaseModel):
    """Body for POST /admin/score/sweep."""

    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description=(
            "Maximum number of postings to score in this call. "
            "Capped at 500 to keep the request within a reasonable timeout window."
        ),
    )
    only_unscored: bool = Field(
        default=True,
        description=(
            "When True (default), only score postings where fit_score IS NULL. "
            "When False, rescore ALL postings regardless — useful after a "
            "weight or extractor change."
        ),
    )


class ScoreDistribution(BaseModel):
    """Per-bucket distribution counts taken after the sweep."""

    by_bucket: dict[str, int] = Field(
        description=(
            "fit_score bucket -> count across ALL job_posting rows. "
            "Buckets: ``0-19``, ``20-39``, ``40-59``, ``60-79``, ``80-100``, "
            "``unscored``."
        ),
    )


class ScoreSweepResponse(BaseModel):
    """Response body for POST /admin/score/sweep."""

    processed: int = Field(description="Number of postings the sweep attempted to score.")
    changed: int = Field(description="Postings whose fit_score changed.")
    skipped: int = Field(
        description="Postings where the scoring function raised; previous score preserved."
    )
    distribution: ScoreDistribution = Field(
        description="Full-table fit_score-bucket snapshot taken after the sweep."
    )
