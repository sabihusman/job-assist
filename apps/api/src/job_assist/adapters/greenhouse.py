"""Greenhouse ATS adapter.

API reference: https://boards-api.greenhouse.io/v1/boards/{handle}/jobs?content=true

Each job object has:
  id            int         → source_job_id
  title         str         → raw_title
  location.name str         → location_raw
  absolute_url  str         → source_url / apply_url
  content       str (HTML)  → jd_text (HTML-stripped)
  first_published ISO str   → posted_at
  updated_at    ISO str     → (ignored; we use our own last_seen_at)
  departments   list        → captured in raw_payload
  offices       list        → captured in raw_payload
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

from job_assist.adapters.base import NormalizedPosting, RawPosting
from job_assist.adapters.normalization import (
    _sha256,
    compute_content_hash,
    detect_role_family,
    detect_seniority,
    normalize_title,
    parse_location,
    strip_html,
)

# Re-exported for backward-compatible imports (tests, downstream code).
__all__ = [
    "GreenhouseAdapter",
    "_sha256",
    "compute_content_hash",
    "detect_role_family",
    "detect_seniority",
    "normalize_title",
    "parse_location",
    "strip_html",
]

# ── Constants ─────────────────────────────────────────────────────────────────

_API_URL = "https://boards-api.greenhouse.io/v1/boards/{handle}/jobs?content=true"


# ── Adapter ───────────────────────────────────────────────────────────────────


class GreenhouseAdapter:
    """Adapter for Greenhouse's public Job Boards API."""

    ats: ClassVar[str] = "greenhouse"
    parser_version: ClassVar[str] = "greenhouse-v1"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self._owns_client = client is None

    async def __aenter__(self) -> GreenhouseAdapter:
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
        """Return all active postings for *handle*, or [] on 404 / error."""
        url = _API_URL.format(handle=handle)
        try:
            resp = await self._get(url)
        except (httpx.HTTPError, httpx.TimeoutException):
            return []
        if resp.status_code != 200:
            return []
        data: dict[str, Any] = resp.json()
        return [
            RawPosting(source_job_id=str(job["id"]), raw_payload=job)
            for job in data.get("jobs", [])
        ]

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Convert a single Greenhouse job object to NormalizedPosting."""
        job = raw.raw_payload
        raw_title: str = job.get("title", "")
        location_raw: str | None = (job.get("location") or {}).get("name")
        html_content: str = job.get("content") or ""

        norm_title = normalize_title(raw_title)
        jd_text = strip_html(html_content)
        locations_normalized, remote_type = parse_location(location_raw)
        seniority = detect_seniority(norm_title)
        role_fam = detect_role_family(norm_title)

        # Timestamps
        posted_at: datetime | None = None
        if raw_ts := job.get("first_published"):
            with contextlib.suppress(ValueError):
                posted_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))

        now = datetime.now(tz=UTC)
        source_url: str = job.get("absolute_url", "")

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
            ats="greenhouse",
            source_job_id=raw.source_job_id,
            source_url=source_url,
            apply_url=source_url or None,
            raw_payload=raw.raw_payload,
            parser_version=self.parser_version,
        )
