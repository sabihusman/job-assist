"""Lever ATS adapter.

API reference: https://api.lever.co/v0/postings/{handle}?mode=json

The response is a flat JSON array. Each posting object has the shape::

    {
      "id":          "<uuid>",                       → source_job_id
      "text":        "Senior Product Manager, Risk", → raw_title
      "categories": {
        "location":     "San Francisco, CA",         → location_raw (fallback)
        "allLocations": ["San Francisco, CA", ...],  → preferred for locations
        "commitment":   "Full-time",                 → raw_payload only
        "department":   "Product",                   → raw_payload only
        "team":         "Risk"                       → raw_payload only
      },
      "hostedUrl":         "https://jobs.lever.co/.../<id>",  → source_url
      "applyUrl":          "https://jobs.lever.co/.../apply", → apply_url
      "descriptionPlain":  "Plain-text JD …",        → jd_text (preferred)
      "description":       "<p>HTML JD …</p>",       → jd_text (fallback)
      "createdAt":         1715000000000,            → posted_at (epoch millis)
      "workplaceType":     "on-site"|"hybrid"|"remote"|"unspecified"
                                                     → remote_type (preferred)
    }
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, ClassVar

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from job_assist.adapters.base import HandleNotFoundError, NormalizedPosting, RawPosting
from job_assist.adapters.normalization import (
    _sha256,
    compute_content_hash,
    detect_role_family,
    detect_seniority,
    normalize_org_field,
    normalize_title,
    parse_location,
    strip_html,
)

_API_URL = "https://api.lever.co/v0/postings/{handle}?mode=json"

# Lever's workplaceType vocabulary → our RemoteType enum.
# `unspecified` (and any unknown value) falls through to keyword-scan
# of the location string in normalize().
_WORKPLACE_TYPE_MAP: dict[str, str] = {
    "on-site": "onsite",
    "onsite": "onsite",
    "hybrid": "hybrid",
    "remote": "remote",
}


def _parse_lever_locations(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Prefer categories.allLocations (list); fall back to categories.location (str).

    Returns (locations_normalized, remote_type_from_location_strings).
    The caller may override the returned remote_type using workplaceType.
    """
    categories: dict[str, Any] = payload.get("categories") or {}
    all_locations = categories.get("allLocations")
    if isinstance(all_locations, list) and all_locations:
        # Join with '/' so parse_location's existing split logic handles each entry.
        joined = " / ".join(str(loc) for loc in all_locations if loc)
        return parse_location(joined)
    return parse_location(categories.get("location"))


class LeverAdapter:
    """Adapter for Lever's public Postings API."""

    ats: ClassVar[str] = "lever"
    parser_version: ClassVar[str] = "lever-v1"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # 60s (not 30s): large boards can exceed 30s from Railway's network
        # path; align all adapters on one headroom value. See Bestiary 5.19.
        self._client = client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        self._owns_client = client is None

    async def __aenter__(self) -> LeverAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        resp = await self._client.get(url)
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        """Return all active postings for *handle*.

        Returns ``[]`` only on non-200 / non-404 responses. Raises
        :class:`HandleNotFoundError` on 404 — an operator-actionable signal
        that the tenant left Lever or the ``ats_handle`` is wrong (Bestiary
        5.9). PROPAGATES a retry-exhausted timeout/HTTPError instead of
        swallowing it as ``[]`` (Bestiary 5.19): a transient failure must
        not look like an empty board, or stale-detection would close every
        posting on it. The orchestrator records it as ``failed``.
        """
        url = _API_URL.format(handle=handle)
        resp = await self._get(url)
        if resp.status_code == 404:
            raise HandleNotFoundError(ats=self.ats, handle=handle, url=url)
        if resp.status_code != 200:
            return []
        data: Any = resp.json()
        if not isinstance(data, list):
            return []
        return [
            RawPosting(source_job_id=str(job["id"]), raw_payload=job)
            for job in data
            if isinstance(job, dict) and job.get("id")
        ]

    def peek_title(self, raw: RawPosting) -> str:
        """Cheap title extraction for the pre-filter — Lever stores the
        title under ``text``, NOT ``title`` (which is empty on most
        Lever payloads). Mirrors the ``normalize()`` extraction so the
        filter never disagrees with the normalized title."""
        job = raw.raw_payload
        return str(job.get("text") or "")

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Convert a single Lever posting object to NormalizedPosting."""
        job = raw.raw_payload
        raw_title: str = str(job.get("text") or "")
        categories: dict[str, Any] = job.get("categories") or {}
        location_raw: str | None = categories.get("location")

        # ── jd_text: prefer descriptionPlain; fall back to HTML stripping ────
        plain = job.get("descriptionPlain")
        if isinstance(plain, str) and plain.strip():
            jd_text = plain.strip()
        else:
            jd_text = strip_html(str(job.get("description") or ""))

        # ── Locations + remote_type ─────────────────────────────────────────
        locations_normalized, derived_remote = _parse_lever_locations(job)

        workplace = str(job.get("workplaceType") or "").lower().strip()
        # "unspecified" / missing / unknown values aren't in the map and fall
        # through to the keyword-derived value from the location string.
        remote_type = _WORKPLACE_TYPE_MAP.get(workplace, derived_remote)

        # ── Title-derived attributes (shared heuristics) ─────────────────────
        norm_title = normalize_title(raw_title)
        seniority = detect_seniority(norm_title)
        role_fam = detect_role_family(norm_title)

        # Lever exposes department + team as siblings of location under
        # `categories`. Both are string-or-null per Lever's docs.
        department = normalize_org_field(categories.get("department"))
        team = normalize_org_field(categories.get("team"))

        # ── Timestamps ──────────────────────────────────────────────────────
        posted_at: datetime | None = None
        raw_created = job.get("createdAt")
        if isinstance(raw_created, int | float) and raw_created > 0:
            with contextlib.suppress(ValueError, OSError, OverflowError):
                posted_at = datetime.fromtimestamp(raw_created / 1000.0, tz=UTC)

        now = datetime.now(tz=UTC)
        source_url: str = str(job.get("hostedUrl") or "")
        apply_url: str | None = (job.get("applyUrl") or None) or (source_url or None)

        return NormalizedPosting(
            canonical_company_name=canonical_company_name,
            normalized_title=norm_title,
            raw_title=raw_title,
            location_raw=location_raw,
            locations_normalized=locations_normalized,
            remote_type=remote_type,
            jd_text=jd_text,
            jd_text_hash=_sha256(jd_text),
            content_hash=compute_content_hash(
                canonical_company_name, norm_title, locations_normalized
            ),
            posted_at=posted_at,
            first_seen_at=now,
            last_seen_at=now,
            seniority_level=seniority,
            role_family=role_fam,
            department=department,
            team=team,
            ats="lever",
            source_job_id=raw.source_job_id,
            source_url=source_url,
            apply_url=apply_url,
            raw_payload=raw.raw_payload,
            parser_version=self.parser_version,
        )
