"""Ashby ATS adapter.

API reference: https://api.ashbyhq.com/posting-api/job-board/{handle}

We always request ``?includeCompensation=true`` so that boards which expose
salary ranges include them in the payload. Boards that don't surface comp
simply omit the ``compensation`` key — that's handled gracefully below.

Each job object has the shape::

    {
      "id":              "<uuid>",
      "title":           "Senior Product Manager",
      "department":      "Product",
      "team":            "Growth",
      "employmentType":  "FullTime",
      "location":        "San Francisco, CA",
      "secondaryLocations": [{"location": "Remote — US"}, ...],
      "isRemote":        true,
      "isListed":        true,        # ← we skip rows where this is false
      "isInternal":      false,       # ← and where this is true
      "publishedAt":     "2026-05-01T12:00:00Z",
      "compensation":    {"compensationTierSummary": "$140K - $180K", ...},
      "jobUrl":          "https://jobs.ashbyhq.com/<handle>/<id>",
      "applyUrl":        "https://jobs.ashbyhq.com/<handle>/<id>/application",
      "descriptionHtml": "<p>…</p>",
      "descriptionPlain": "…",
    }
"""

from __future__ import annotations

import contextlib
import logging
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
    parse_compensation,
    parse_location,
    strip_html,
)

logger = logging.getLogger(__name__)

_API_URL = "https://api.ashbyhq.com/posting-api/job-board/{handle}?includeCompensation=true"


def _collect_ashby_locations(job: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Combine primary + secondaryLocations into a single normalised list.

    Returns (locations_normalized, keyword_derived_remote_type). The caller is
    responsible for overriding remote_type when isRemote=true.
    """
    parts: list[str] = []
    primary = job.get("location")
    if isinstance(primary, str) and primary.strip():
        parts.append(primary.strip())

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            loc = secondary.get("location")
            if isinstance(loc, str) and loc.strip():
                parts.append(loc.strip())

    if not parts:
        return [], "unknown"
    # parse_location already handles "/"-separated input across all parts.
    return parse_location(" / ".join(parts))


class AshbyAdapter:
    """Adapter for Ashby's public Job Board API."""

    ats: ClassVar[str] = "ashby"
    parser_version: ClassVar[str] = "ashby-v1"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # 60s (not 30s): Ashby's largest boards (Notion, Plaid, Ramp, Vanta)
        # return ~2MB payloads that intermittently exceed 30s from Railway's
        # network path. See Bestiary 5.19.
        self._client = client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        self._owns_client = client is None

    async def __aenter__(self) -> AshbyAdapter:
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
        """Return public, externally-listed postings for *handle*.

        Filters out rows with ``isListed=false`` or ``isInternal=true`` so
        unpublished or employees-only roles never enter our pipeline.

        Bestiary 5.19: a retry-exhausted timeout/HTTPError PROPAGATES — it is
        NOT swallowed as ``[]``. Returning an empty list on a transient
        network failure is indistinguishable from a genuinely empty board,
        and (with stale-detection) would falsely close every posting on it.
        The orchestrator records the raised error as ``status='failed'``.
        """
        url = _API_URL.format(handle=handle)
        resp = await self._get(url)
        if resp.status_code == 404:
            # Bestiary 5.9 — distinguish stale handle from generic empty.
            raise HandleNotFoundError(ats=self.ats, handle=handle, url=url)
        if resp.status_code != 200:
            return []
        data: Any = resp.json()
        if not isinstance(data, dict):
            return []
        jobs = data.get("jobs")
        if not isinstance(jobs, list):
            return []

        out: list[RawPosting] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if job.get("isListed") is False:
                continue
            if job.get("isInternal") is True:
                continue
            job_id = job.get("id")
            if not job_id:
                continue
            out.append(RawPosting(source_job_id=str(job_id), raw_payload=job))
        return out

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Convert a single Ashby job object to NormalizedPosting."""
        job = raw.raw_payload
        raw_title: str = str(job.get("title") or "")
        location_raw: str | None = job.get("location") or None

        # ── jd_text: prefer descriptionPlain, fall back to HTML strip ────────
        plain = job.get("descriptionPlain")
        if isinstance(plain, str) and plain.strip():
            jd_text = plain.strip()
        else:
            jd_text = strip_html(str(job.get("descriptionHtml") or ""))

        # ── Locations + remote_type ─────────────────────────────────────────
        locations_normalized, derived_remote = _collect_ashby_locations(job)
        remote_type = "remote" if job.get("isRemote") is True else derived_remote

        # ── Compensation ────────────────────────────────────────────────────
        comp = job.get("compensation") or {}
        comp_summary = comp.get("compensationTierSummary") if isinstance(comp, dict) else None
        salary_min, salary_max, salary_currency, salary_period_str = parse_compensation(
            comp_summary if isinstance(comp_summary, str) else None
        )
        if comp_summary and salary_min is None and salary_max is None:
            logger.warning(
                "ashby.compensation.unparsed",
                extra={"summary": comp_summary, "ashby_job_id": job.get("id")},
            )
        salary_period = salary_period_str or "unknown"

        # ── Title-derived attributes (shared heuristics) ─────────────────────
        norm_title = normalize_title(raw_title)
        seniority = detect_seniority(norm_title)
        role_fam = detect_role_family(norm_title)

        # Ashby surfaces ``department`` and ``team`` as siblings of ``title``
        # on the job object. Both are plain string-or-null.
        department = normalize_org_field(job.get("department"))
        team = normalize_org_field(job.get("team"))

        # ── Timestamps ──────────────────────────────────────────────────────
        posted_at: datetime | None = None
        if raw_ts := job.get("publishedAt"):
            with contextlib.suppress(ValueError):
                posted_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))

        now = datetime.now(tz=UTC)
        source_url: str = str(job.get("jobUrl") or "")
        apply_url: str | None = (job.get("applyUrl") or None) or (source_url or None)

        return NormalizedPosting(
            canonical_company_name=canonical_company_name,
            normalized_title=norm_title,
            raw_title=raw_title,
            location_raw=location_raw,
            locations_normalized=locations_normalized,
            remote_type=remote_type,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
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
            ats="ashby",
            source_job_id=raw.source_job_id,
            source_url=source_url,
            apply_url=apply_url,
            raw_payload=raw.raw_payload,
            parser_version=self.parser_version,
        )
