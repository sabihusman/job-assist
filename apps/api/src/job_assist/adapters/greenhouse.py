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
import hashlib
import json
import re
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

# ── Constants ─────────────────────────────────────────────────────────────────

_API_URL = "https://boards-api.greenhouse.io/v1/boards/{handle}/jobs?content=true"

# Abbreviation expansions applied before lowercasing (order matters: APM before PM).
_ABBREVS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bAPM\b"), "associate product manager"),
    (re.compile(r"\bPM\b"), "product manager"),
    (re.compile(r"\bSr\.?\s*", re.IGNORECASE), "senior "),
    (re.compile(r"\bJr\.?\s*", re.IGNORECASE), "junior "),
    (re.compile(r"\bVP\b"), "vice president"),
    (re.compile(r"\bGM\b"), "general manager"),
]


# ── Title helpers ─────────────────────────────────────────────────────────────


def _expand_abbrevs(title: str) -> str:
    for pattern, replacement in _ABBREVS:
        title = pattern.sub(replacement, title)
    return title


def normalize_title(raw_title: str) -> str:
    """Lowercase + expand abbreviations + collapse whitespace."""
    title = _expand_abbrevs(raw_title)
    title = title.lower()
    return re.sub(r"\s+", " ", title).strip()


# ── HTML stripping ─────────────────────────────────────────────────────────────


def strip_html(html: str) -> str:
    """Strip HTML to plain text using selectolax; preserve logical line breaks."""
    if not html:
        return ""
    try:
        from selectolax.parser import HTMLParser

        parser = HTMLParser(html)
        text = parser.text(separator="\n")
    except Exception:
        # Fallback: crude regex stripping if selectolax fails unexpectedly.
        text = re.sub(r"<[^>]+>", " ", html)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ── Location parsing ──────────────────────────────────────────────────────────


def parse_location(
    location_raw: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (locations_normalized, remote_type_str).

    Heuristics (intentionally lo-fi for Week 1):
      - "Remote"              → [{remote_type: remote}]          → remote
      - "City, ST"            → [{city, state, country: US, remote_type: onsite}]
      - Multiple via '/' ';'  → split and parse each part
      - Anything else         → [{city: raw, remote_type: unknown}]
    """
    if not location_raw:
        return [], "unknown"

    parts = [p.strip() for p in re.split(r"[/;]", location_raw) if p.strip()]
    results: list[dict[str, Any]] = []

    for part in parts:
        if re.search(r"\bremote\b", part, re.IGNORECASE):
            results.append({"remote_type": "remote"})
        else:
            m = re.match(r"^(.+?),\s*([A-Z]{2})$", part)
            if m:
                results.append(
                    {
                        "city": m.group(1).strip(),
                        "state": m.group(2),
                        "country": "US",
                        "remote_type": "onsite",
                    }
                )
            else:
                results.append({"city": part, "remote_type": "unknown"})

    if not results:
        return [], "unknown"

    if any(r.get("remote_type") == "remote" for r in results):
        remote_type = "remote"
    elif all(r.get("remote_type") == "onsite" for r in results):
        remote_type = "onsite"
    else:
        remote_type = "unknown"

    return results, remote_type


# ── Seniority / role family ───────────────────────────────────────────────────


def detect_seniority(normalized_title: str) -> str:
    """Derive SeniorityLevel enum value from a normalised title string."""
    t = normalized_title
    if "intern" in t:
        return "intern"
    if "principal" in t:
        return "principal_pm"
    if "staff" in t or re.search(r"\blead\b", t):
        return "lead_pm"
    if "senior" in t:
        return "senior_pm"
    if "associate" in t or "apm" in t:
        return "apm"
    if "product manager" in t or "product owner" in t or "product management" in t:
        return "pm"
    return "unknown"


def detect_role_family(normalized_title: str) -> str:
    """Derive RoleFamily enum value from a normalised title string."""
    t = normalized_title
    if "product marketing" in t:
        return "product_marketing"
    if "product owner" in t:
        return "product_owner"
    if "program manager" in t or "program management" in t:
        return "program_management"
    if "product manager" in t or "product management" in t:
        return "product_management"
    return "other"


# ── Hashing ───────────────────────────────────────────────────────────────────


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_content_hash(
    canonical_company_name: str,
    normalized_title: str,
    locations_normalized: list[dict[str, Any]],
) -> str:
    """Stable hash over (company, title, locations) — identifies a unique role."""
    payload = json.dumps(
        {
            "company": canonical_company_name,
            "title": normalized_title,
            "locations": locations_normalized,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256(payload)


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
