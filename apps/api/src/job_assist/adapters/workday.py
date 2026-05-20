"""Workday ATS adapter (PR #33).

Workday is per-tenant rather than centralized: each customer has its own
subdomain shard. The public job-board API for a tenant lives at::

    POST  https://{tenant}.{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    GET   https://{tenant}.{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{externalPath}

``{tenant}`` is the ``ats_handle`` on ``target_company`` (e.g. ``"jpmc"``).
``{wd_number}`` is the shard (``"wd1"``, ``"wd5"``, …) and ``{site}`` is
the per-tenant career-site identifier (commonly ``"External"``). Both
come from ``target_company.adapter_config`` JSONB — see PR #33's
migration ``b3d8e9c4f5a1``.

List-endpoint response shape (verified against jpmc.wd5 + capitalone.wd1
during read-first; trimmed to fields we read)::

    {
      "total": 1234,
      "jobPostings": [
        {
          "title":           "Senior Product Manager",
          "externalPath":    "/job/.../Senior-Product-Manager_R-12345",
          "locationsText":   "New York, NY",
          "postedOn":        "Posted Today" | "Posted 5 Days Ago" | "...",
          "bulletFields":    ["R-12345"],            # job req id is bulletFields[0]
          "remoteType":      "remote",               # optional
          "jobFamily":       "Product",              # optional
          ...
        },
        ...
      ]
    }

Detail-endpoint response shape::

    {
      "jobPostingInfo": {
        "title":          "Senior Product Manager",
        "jobDescription": "<p>…</p>",                # HTML
        "jobReqId":       "R-12345",
        "location":       "New York, NY",
        "postedOn":       "Posted Today",
        "remoteType":     "Remote",                  # optional
        "externalUrl":    "https://...myworkdayjobs.com/en-US/.../R-12345",
        "jobFamily":      "Product",                 # optional
        "department":     "Technology",              # rare
        ...
      },
      "hiringOrganization": { "name": "JPMorgan Chase" },
      ...
    }

The detail endpoint is hit per-job to pick up the full HTML body. The
list-endpoint payload is also stored in ``posting_source.raw_payload``
so we can re-derive fields later without another network hop.

Discover-ats: Workday's URL shape makes auto-detection by name probe
impractical (would need to brute-force tenant + shard + site triples).
Operators add Workday rows manually via SQL — see the PR description
for example INSERTs. The ``detect_workday_url`` helper here can be
called by future enhancements to parse a manually-provided URL.
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta
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
    normalize_org_field,
    normalize_title,
    parse_location,
    strip_html,
)

logger = logging.getLogger(__name__)

# Per-tenant request page size. Workday's list endpoint accepts up to
# ~100 in practice; pick 50 for politeness + faster failure recovery.
_PAGE_SIZE = 50

# Bound the pagination loop. 50 pages * 50 = 2500 jobs per tenant —
# beyond that we'd accept the cap and let the next cron pick up the rest.
_MAX_PAGES = 50

# Match a Workday career-site URL: any subdomain ending in
# ``.myworkdayjobs.com``. Used by `detect_workday_url`.
_WORKDAY_HOST_RE = re.compile(
    r"^(?:https?://)?(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com(?P<path>/.*)?$",
    re.IGNORECASE,
)


def detect_workday_url(url: str) -> dict[str, str] | None:
    """If *url* is a Workday career-site URL, return its parts; else None.

    Returns ``{"tenant", "wd_number", "site"}``. The site is inferred
    from the path (``/External``, ``/en-US/External``, …); falls back to
    ``"External"`` when undetectable.
    """
    if not url:
        return None
    m = _WORKDAY_HOST_RE.match(url.strip())
    if not m:
        return None
    tenant = m.group("tenant").lower()
    wd = m.group("wd").lower()
    site = _site_from_path(m.group("path") or "")
    return {"tenant": tenant, "wd_number": wd, "site": site}


def _site_from_path(path: str) -> str:
    """Parse the career-site identifier out of a Workday URL path.

    Paths look like ``/External`` or ``/en-US/External/job/...`` —
    take the last non-locale segment. Locale prefixes match ``xx-XX``.
    """
    if not path:
        return "External"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "External"
    # Drop a leading locale segment if present (e.g. "en-US").
    if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segments[0]):
        segments = segments[1:]
    return segments[0] if segments else "External"


def _build_jobs_url(tenant: str, wd_number: str, site: str) -> str:
    return f"https://{tenant}.{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def _build_detail_url(tenant: str, wd_number: str, site: str, external_path: str) -> str:
    # `external_path` from the list response looks like
    # "/job/.../Senior-Product-Manager_R-12345". The detail endpoint
    # mirrors the list URL prefix plus the external path.
    suffix = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"https://{tenant}.{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{suffix}"


def _parse_posted_on(label: str | None) -> datetime | None:
    """Best-effort parser for Workday's ``postedOn`` strings.

    The endpoint returns relative-time strings like:
       "Posted Today" | "Posted Yesterday" | "Posted 5 Days Ago"
       "Posted 30+ Days Ago" | "Posted More Than 30 Days Ago"

    Convert to a UTC datetime anchored to *now*. ``None`` when the
    label can't be parsed (defensive — the field is informational, not
    load-bearing).
    """
    if not isinstance(label, str) or not label.strip():
        return None
    s = label.strip().lower()
    now = datetime.now(tz=UTC)
    if "today" in s:
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\s*\+?\s*day", s)
    if m:
        try:
            return now - timedelta(days=int(m.group(1)))
        except ValueError:
            return None
    if "more than 30" in s or "30+" in s:
        return now - timedelta(days=30)
    return None


def _extract_req_id(raw_job: dict[str, Any], raw_detail: dict[str, Any]) -> str | None:
    """Pull a stable job-req id from either the list row or detail body.

    The list row's ``bulletFields`` is usually a one-element array
    containing the req id (e.g. ``["R-12345"]``); the detail's
    ``jobPostingInfo.jobReqId`` is the same string. Prefer the detail
    side when present since it's the canonical source.
    """
    info = raw_detail.get("jobPostingInfo") if isinstance(raw_detail, dict) else None
    if isinstance(info, dict):
        req = info.get("jobReqId")
        if isinstance(req, str) and req.strip():
            return req.strip()
    bullets = raw_job.get("bulletFields")
    if isinstance(bullets, list) and bullets:
        first = bullets[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def _extract_department_team(raw_detail: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best-effort department + team extraction from the detail payload.

    Workday's ``jobPostingInfo`` doesn't have a stable department field
    across tenants. We try, in order:
      1. ``jobPostingInfo.department``
      2. ``jobPostingInfo.jobFamily``
      3. ``jobPostingInfo.classificationHierarchies[0].name``

    Team is rarely surfaced — we don't attempt to derive it.
    """
    info = raw_detail.get("jobPostingInfo") if isinstance(raw_detail, dict) else None
    if not isinstance(info, dict):
        return None, None
    for key in ("department", "jobFamily"):
        v = info.get(key)
        if isinstance(v, str) and v.strip():
            return normalize_org_field(v), None
    classifications = info.get("classificationHierarchies")
    if isinstance(classifications, list) and classifications:
        first = classifications[0]
        if isinstance(first, dict):
            name = first.get("name")
            if isinstance(name, str) and name.strip():
                return normalize_org_field(name), None
    return None, None


class WorkdayAdapter:
    """Adapter for Workday's public career-site CXS API.

    Construct with the *target_company's* ``adapter_config`` dict — the
    fetch methods read ``wd_number`` and ``site`` off the instance, so
    one adapter instance serves one tenant.
    """

    ats: ClassVar[str] = "workday"
    parser_version: ClassVar[str] = "workday-v1"

    def __init__(
        self,
        adapter_config: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = adapter_config or {}
        self.wd_number: str = str(cfg.get("wd_number") or "wd1")
        self.site: str = str(cfg.get("site") or "External")
        self._client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self._owns_client = client is None

    async def __aenter__(self) -> WorkdayAdapter:
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
    async def _post(self, url: str, body: dict[str, Any]) -> httpx.Response:
        resp = await self._client.post(url, json=body)
        if resp.status_code in (429, 503) or resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        resp = await self._client.get(url)
        if resp.status_code in (429, 503) or resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    async def _fetch_page(
        self,
        handle: str,
        offset: int,
        limit: int = _PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        url = _build_jobs_url(handle, self.wd_number, self.site)
        body: dict[str, Any] = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
        try:
            resp = await self._post(url, body)
        except (httpx.HTTPError, httpx.TimeoutException):
            return []
        if resp.status_code != 200:
            return []
        data: Any = resp.json()
        if not isinstance(data, dict):
            return []
        postings = data.get("jobPostings")
        if not isinstance(postings, list):
            return []
        return [p for p in postings if isinstance(p, dict)]

    async def _fetch_detail(self, handle: str, external_path: str) -> dict[str, Any]:
        url = _build_detail_url(handle, self.wd_number, self.site, external_path)
        try:
            resp = await self._get(url)
        except (httpx.HTTPError, httpx.TimeoutException):
            return {}
        if resp.status_code != 200:
            return {}
        data: Any = resp.json()
        return data if isinstance(data, dict) else {}

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        """Fetch all active postings for *handle* (the Workday tenant id).

        Walks pages until an empty page is returned or ``_MAX_PAGES``
        is reached. Each posting includes the detail payload merged into
        ``raw_payload['detail']`` so ``normalize`` can read both shapes
        from a single dict.
        """
        out: list[RawPosting] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            page = await self._fetch_page(handle, offset)
            if not page:
                break
            for job in page:
                external_path = job.get("externalPath")
                if not isinstance(external_path, str) or not external_path.strip():
                    continue
                detail = await self._fetch_detail(handle, external_path)
                # `bulletFields[0]` is the req id; falls back to the
                # externalPath hash if missing.
                req_id = _extract_req_id(job, detail) or external_path
                merged: dict[str, Any] = {"list": job, "detail": detail}
                out.append(RawPosting(source_job_id=str(req_id), raw_payload=merged))
            offset += len(page)
        return out

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Map a merged Workday list + detail payload to NormalizedPosting."""
        payload = raw.raw_payload
        job = payload.get("list") if isinstance(payload, dict) else {}
        detail = payload.get("detail") if isinstance(payload, dict) else {}
        if not isinstance(job, dict):
            job = {}
        if not isinstance(detail, dict):
            detail = {}

        info_any = detail.get("jobPostingInfo")
        info: dict[str, Any] = info_any if isinstance(info_any, dict) else {}

        # ── Title + location ────────────────────────────────────────────────
        raw_title = str(info.get("title") or job.get("title") or "")
        location_raw_value = info.get("location") or job.get("locationsText") or None
        location_raw: str | None = (
            str(location_raw_value) if isinstance(location_raw_value, str) else None
        )

        # ── JD: prefer detail HTML, strip to plain text ─────────────────────
        jd_html_raw = info.get("jobDescription") or ""
        jd_text = strip_html(str(jd_html_raw)) if jd_html_raw else ""

        # ── Locations + remote_type ─────────────────────────────────────────
        locations_normalized, derived_remote = parse_location(location_raw)
        remote_label = info.get("remoteType") or job.get("remoteType")
        if isinstance(remote_label, str) and "remote" in remote_label.lower():
            remote_type = "remote"
        elif isinstance(remote_label, str) and "hybrid" in remote_label.lower():
            remote_type = "hybrid"
        else:
            remote_type = derived_remote

        # Workday rarely exposes salary on the public CXS endpoints —
        # leave as NULL / unknown.
        salary_min: int | None = None
        salary_max: int | None = None
        salary_currency: str | None = None
        salary_period = "unknown"

        # ── Title-derived attributes ────────────────────────────────────────
        norm_title = normalize_title(raw_title)
        seniority = detect_seniority(norm_title)
        role_fam = detect_role_family(norm_title)

        # ── Department / team ───────────────────────────────────────────────
        department, team = _extract_department_team(detail)

        # ── Timestamps ──────────────────────────────────────────────────────
        posted_at = _parse_posted_on(info.get("postedOn") or job.get("postedOn"))

        # Detail's externalUrl is the canonical career-site URL; fall back
        # to constructing one from the externalPath if missing.
        external_url = info.get("externalUrl")
        if not isinstance(external_url, str) or not external_url.strip():
            external_path = job.get("externalPath")
            if isinstance(external_path, str) and external_path.strip():
                external_url = f"https://workday-job-detail{external_path}"
            else:
                external_url = ""
        source_url = external_url

        with contextlib.suppress(Exception):
            # Surface unusable JD bodies (HTML present but stripped to empty
            # string) so we can spot adapter regressions during ingest runs.
            if jd_html_raw and not jd_text:
                logger.warning(
                    "workday.jd.empty_after_strip",
                    extra={"req_id": raw.source_job_id, "title": raw_title},
                )

        now = datetime.now(tz=UTC)

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
            ats="workday",
            source_job_id=raw.source_job_id,
            source_url=source_url,
            apply_url=source_url or None,
            raw_payload=raw.raw_payload,
            parser_version=self.parser_version,
        )
