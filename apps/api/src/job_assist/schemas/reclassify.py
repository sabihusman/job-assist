"""Pydantic schemas for the /admin/reclassify/sweep endpoint (PR #48)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReclassifySweepRequest(BaseModel):
    """Body for POST /admin/reclassify/sweep."""

    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description=(
            "Maximum number of postings to process in this call. "
            "Capped at 500 to keep the request within a reasonable timeout window."
        ),
    )
    only_unclassified: bool = Field(
        default=True,
        description=(
            "When True (default), only touch postings where role_family='other' "
            "OR seniority_level='unknown' (the buckets the regex assigns when it "
            "can't determine a value). "
            "When False, reclassify ALL postings regardless of current values — "
            "useful for a full re-sweep after a prompt upgrade."
        ),
    )


class ReclassifySweepResponse(BaseModel):
    """Response body for POST /admin/reclassify/sweep."""

    processed: int = Field(description="Number of postings the sweep attempted to classify.")
    changed: int = Field(description="Postings where at least one field changed.")
    skipped: int = Field(
        description="Postings where the LLM call failed; original values preserved."
    )
    distribution: ReclassifyDistribution = Field(
        description="Full-table distribution snapshot taken after the sweep."
    )


class ReclassifyDistribution(BaseModel):
    """Per-enum distribution counts taken after the sweep."""

    role_family: dict[str, int] = Field(
        description="role_family → count across ALL job_posting rows."
    )
    seniority: dict[str, int] = Field(
        description="seniority_level → count across ALL job_posting rows."
    )
