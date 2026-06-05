"""iCIMS ATS adapter (PR #55).

iCIMS is the second-most-common enterprise ATS after Workday. Unlike
Workday's per-tenant JSON CXS endpoints, iCIMS career sites are
server-rendered HTML. The highest-fidelity structured surface is the
``<script type="application/ld+json">`` block on each detail page,
which carries a `JobPosting` schema-org object — title, description,
location, datePosted, hiringOrganization, sometimes baseSalary.

URL shape (documented; verify against first real handle after merge —
see the bestiary entry on hand-authored fixtures)::

    https://careers-{handle}.icims.com                       — listing root
    https://careers-{handle}.icims.com/jobs/search?ss=1...   — listing page
    https://careers-{handle}.icims.com/jobs/{id}/{slug}/job  — detail page

``{handle}`` is the ``ats_handle`` on ``target_company`` (the subdomain
owner, e.g. ``"acmecorp"``). Tenants who serve iCIMS from a non-default
URL (CNAME like ``jobs.example.com``) can override via
``target_company.adapter_config = {"careers_url": "https://..."}``.
Unlike Workday, ``adapter_config`` is OPTIONAL for iCIMS — the default
URL works for the majority of customers.

Bestiary note (PR #55): the fixtures backing this adapter's tests are
HAND-AUTHORED. They reflect documented iCIMS HTML/JSON-LD shape but
were not captured from a live page during development. The first real
iCIMS handle ingested after merge is the truth check — if the parser
extracts zero rows from a live page, the listing HTML structure differs
from the fixture and the per-handle row selector here is the place to
adjust.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, ClassVar

import httpx
from selectolax.parser import HTMLParser
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from job_assist.adapters.base import (
    BROWSER_HEADERS,
    HandleNotFoundError,
    NormalizedPosting,
    RawPosting,
)
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

# Bound the per-handle pagination loop. iCIMS exposes more rows per page
# than Workday (~25-50 depending on tenant), so 50 pages * 50 rows = 2500
# is a soft cap that matches Workday's bound. The next cron picks up the
# rest if a tenant somehow has more.
_MAX_PAGES = 50

# Path-segment pattern: ``/jobs/{id}/{slug}/job`` is iCIMS's canonical
# detail URL shape. ``{id}`` is a positive integer string — captured by
# the listing parser as the stable ``source_job_id``.
_DETAIL_PATH_RE = re.compile(r"/jobs/(?P<id>\d+)/[^/]+/job(?:\?|/|$)")


def detect_icims_url(url: str) -> str | None:
    """If *url* is an iCIMS career-site URL, return the ``handle`` part.

    Recognises the canonical ``careers-<handle>.icims.com`` shape and the
    less-common ``<handle>.icims.com`` form. Returns ``None`` for
    non-iCIMS URLs. Helps the discover-ats probe identify iCIMS hosts
    automatically; not load-bearing for the adapter itself.
    """
    if not url:
        return None
    m = re.match(
        r"^(?:https?://)?(?:careers-)?(?P<handle>[a-z0-9-]+)\.icims\.com(?:/.*)?$",
        url.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    return m.group("handle").lower()


def _default_careers_url(handle: str) -> str:
    """Canonical iCIMS careers root for a given handle."""
    return f"https://careers-{handle}.icims.com"


def _build_listing_url(careers_url: str, offset: int) -> str:
    """iCIMS listing URL. ``in_iframe=1`` strips the chrome so the HTML
    contains only the rows we want."""
    # iCIMS exposes pagination via ``searchRelation`` + offset cursors,
    # but a simpler ``ss=1&hashed=-1`` listing returns all jobs as a
    # paged HTML response. The offset query param is honored by most
    # tenants — degrades gracefully to "first page only" on tenants
    # that ignore it (in which case _MAX_PAGES caps the wasted work).
    base = careers_url.rstrip("/")
    return (
        f"{base}/jobs/search?ss=1&hashed=-1&in_iframe=1&searchRelation=keyword_all&offset={offset}"
    )


def _build_detail_url(careers_url: str, source_job_id: str, slug: str) -> str:
    """Reconstruct an iCIMS detail URL from the listing-extracted parts."""
    base = careers_url.rstrip("/")
    return f"{base}/jobs/{source_job_id}/{slug}/job"


def _extract_listing_rows(html: str) -> list[dict[str, str]]:
    """Parse a listing HTML page into ``{source_job_id, slug, raw_title}`` rows.

    Reads ``<a>`` tags whose href matches the canonical detail-path
    pattern. The href carries the ``source_job_id`` and ``slug`` we
    need to construct the detail URL.

    Returns ``[]`` on parse failure or empty page — the caller treats
    that as "stop paginating."
    """
    if not html:
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        tree = HTMLParser(html)
    except Exception:  # pragma: no cover — defensive
        logger.warning("icims.listing.parse_failed")
        return []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        m = _DETAIL_PATH_RE.search(href)
        if not m:
            continue
        source_job_id = m.group("id")
        if source_job_id in seen:
            continue
        seen.add(source_job_id)
        # Slug is the path segment between {id} and "/job".
        # Best-effort extraction; falls back to "job" if absent.
        slug_match = re.search(r"/jobs/\d+/([^/]+)/job", href)
        slug = slug_match.group(1) if slug_match else "job"
        title = (anchor.text() or "").strip()
        rows.append(
            {
                "source_job_id": source_job_id,
                "slug": slug,
                "raw_title": title,
            }
        )
    return rows


def _extract_jsonld(html: str) -> dict[str, Any] | None:
    """Pull the JobPosting JSON-LD block out of a detail-page HTML.

    iCIMS detail pages include one or more ``<script type="application/
    ld+json">`` blocks; we pick the first whose ``@type`` is
    ``JobPosting``. Returns ``None`` if none found / unparseable —
    callers degrade to whatever fields they can derive without it.
    """
    if not html:
        return None
    try:
        tree = HTMLParser(html)
    except Exception:  # pragma: no cover
        return None
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text() or ""
        if not text.strip():
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            # Some pages put the JobPosting inside a list of @graph
            # entries. Walk both shapes.
            for entry in payload:
                if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                    return entry
            continue
        if isinstance(payload, dict):
            if payload.get("@type") == "JobPosting":
                return payload
            graph = payload.get("@graph")
            if isinstance(graph, list):
                for entry in graph:
                    if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                        return entry
    return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Best-effort parser for ISO-8601 strings from JSON-LD ``datePosted``.

    iCIMS JSON-LD typically carries either ``YYYY-MM-DD`` or a full
    ISO datetime. Both ``date.fromisoformat`` and
    ``datetime.fromisoformat`` accept these as of Python 3.11.
    Returns ``None`` for anything unparseable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    # ``YYYY-MM-DD`` → coerce to midnight UTC.
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=UTC)
        # Replace trailing Z with +00:00 for fromisoformat compat.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _extract_location_from_jsonld(jsonld: dict[str, Any]) -> str | None:
    """Pull a human-readable location string from JSON-LD.

    JSON-LD ``jobLocation`` is typically a dict (or list of dicts) with
    a nested ``address`` containing ``addressLocality`` /
    ``addressRegion`` / ``addressCountry``. Concat the present parts.
    """
    loc = jsonld.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return None
    address = loc.get("address") or {}
    if not isinstance(address, dict):
        return None
    parts: list[str] = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        v = address.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return ", ".join(parts) if parts else None


def _extract_salary_from_jsonld(
    jsonld: dict[str, Any],
) -> tuple[int | None, int | None, str | None]:
    """Pull ``(salary_min, salary_max, currency)`` from JSON-LD if present.

    JSON-LD ``baseSalary`` shape::

        {"@type": "MonetaryAmount",
         "currency": "USD",
         "value": {"@type": "QuantitativeValue",
                   "minValue": 120000, "maxValue": 180000,
                   "unitText": "YEAR"}}

    Many iCIMS tenants omit this entirely — that's expected, NULL
    salary is the dominant signal. ``unitText`` is informational; we
    only return values that look like annual figures (>= 1000).
    """
    base = jsonld.get("baseSalary")
    if not isinstance(base, dict):
        return None, None, None
    currency = base.get("currency")
    currency_str = str(currency) if isinstance(currency, str) and currency.strip() else None
    value = base.get("value")
    if not isinstance(value, dict):
        # Some pages give a flat numeric `value` instead of a QuantitativeValue.
        flat = base.get("value")
        if isinstance(flat, (int, float)) and flat >= 1000:
            n = int(flat)
            return n, n, currency_str
        return None, None, currency_str
    smin = value.get("minValue")
    smax = value.get("maxValue")
    smin_int = int(smin) if isinstance(smin, (int, float)) and smin >= 1000 else None
    smax_int = int(smax) if isinstance(smax, (int, float)) and smax >= 1000 else None
    return smin_int, smax_int, currency_str


def _extract_department_team_from_jsonld(
    jsonld: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Best-effort department + team. iCIMS surfaces these less
    consistently than Workday — typically inside ``industry`` or
    ``occupationalCategory`` fields. We don't attempt to extract team.
    """
    for key in ("industry", "occupationalCategory"):
        v = jsonld.get(key)
        if isinstance(v, str) and v.strip():
            return normalize_org_field(v), None
    return None, None


class ICIMSAdapter:
    """Adapter for iCIMS career-site HTML + JSON-LD parsing.

    Public surface mirrors :class:`WorkdayAdapter` exactly:
    ``async fetch_postings(handle)`` and ``normalize(raw, name)``,
    async context manager. The internal implementation differs because
    iCIMS does not expose a public JSON listing API.

    The ``careers_url`` override lives on ``target_company.adapter_config``
    as ``{"careers_url": "https://jobs.example.com"}``. If absent, the
    adapter falls back to ``https://careers-{handle}.icims.com`` (the
    documented default).
    """

    ats: ClassVar[str] = "icims"
    parser_version: ClassVar[str] = "icims-v1"

    def __init__(
        self,
        adapter_config: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = adapter_config or {}
        self.careers_url_override: str | None = (
            str(cfg.get("careers_url")) if cfg.get("careers_url") else None
        )
        # 60s (not 30s): large tenants paginate many detail fetches; align
        # all adapters on one headroom value. See Bestiary 5.19.
        # feat/datacenter-egress-headers: browser-like headers — iCIMS serves
        # empty/challenge HTML to the default python-httpx UA from datacenter IPs.
        self._client = client or httpx.AsyncClient(
            timeout=60.0, follow_redirects=True, headers=BROWSER_HEADERS
        )
        self._owns_client = client is None

    async def __aenter__(self) -> ICIMSAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _careers_url(self, handle: str) -> str:
        return self.careers_url_override or _default_careers_url(handle)

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        resp = await self._client.get(url)
        if resp.status_code in (429, 503) or resp.status_code >= 500:
            # tenacity sees the HTTPError and retries; same contract as
            # Workday adapter.
            resp.raise_for_status()
        return resp

    async def _fetch_listing(
        self,
        careers_url: str,
        offset: int,
        *,
        handle: str,
    ) -> list[dict[str, str]]:
        """Fetch one page of listing rows.

        Raises :class:`HandleNotFoundError` on 404 when ``offset == 0``
        — the careers URL doesn't resolve to a tenant. Mid-pagination
        404 is treated as an empty page (rare; tenant disappearing).
        See Bestiary 5.9.

        Bestiary 5.19: a retry-exhausted timeout/HTTPError PROPAGATES (not
        swallowed as ``[]``). A listing-fetch failure must not look like
        "end of pagination" / "empty board" — that truncates or empties the
        board, and stale-detection would then close the un-fetched postings.
        The orchestrator records the raised error as ``failed``.
        """
        url = _build_listing_url(careers_url, offset)
        resp = await self._get(url)
        if resp.status_code == 404 and offset == 0:
            raise HandleNotFoundError(ats=self.ats, handle=handle, url=url)
        if resp.status_code != 200:
            return []
        return _extract_listing_rows(resp.text or "")

    async def _fetch_detail_html(self, careers_url: str, source_job_id: str, slug: str) -> str:
        # Unlike the listing fetch, a per-detail failure stays lenient
        # (returns ``""``): the posting still enters the pipeline from the
        # listing row, so ``last_seen_at`` refreshes and stale-detection is
        # not misled — only this row's JD/salary is degraded. Re-raising here
        # would fail an entire board over one slow detail page (Bestiary 5.19
        # scope boundary: re-raise only where the WHOLE board would vanish).
        url = _build_detail_url(careers_url, source_job_id, slug)
        try:
            resp = await self._get(url)
        except (httpx.HTTPError, httpx.TimeoutException):
            return ""
        if resp.status_code != 200:
            return ""
        return resp.text or ""

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        """Fetch all active postings for *handle*.

        Walks listing pages by offset until an empty page is returned
        (or ``_MAX_PAGES`` is hit). For each row, fetches the detail
        HTML and stores ``{"listing_row": ..., "detail_html": ...,
        "jsonld": ...}`` in ``raw_payload`` so ``normalize()`` can read
        both shapes from a single dict without re-fetching.
        """
        careers_url = self._careers_url(handle)
        out: list[RawPosting] = []
        seen_ids: set[str] = set()
        offset = 0
        for _ in range(_MAX_PAGES):
            rows = await self._fetch_listing(careers_url, offset, handle=handle)
            if not rows:
                break
            # Detect tenants that ignore the offset param — if every row
            # on this page was already seen on a previous page, we're
            # stuck in a loop. Stop paginating.
            new_rows = [r for r in rows if r["source_job_id"] not in seen_ids]
            if not new_rows:
                break
            for row in new_rows:
                seen_ids.add(row["source_job_id"])
                detail_html = await self._fetch_detail_html(
                    careers_url, row["source_job_id"], row["slug"]
                )
                jsonld = _extract_jsonld(detail_html)
                merged: dict[str, Any] = {
                    "listing_row": row,
                    "detail_html": detail_html,
                    "jsonld": jsonld or {},
                    "careers_url": careers_url,
                }
                out.append(RawPosting(source_job_id=row["source_job_id"], raw_payload=merged))
            offset += len(rows)
        return out

    def peek_title(self, raw: RawPosting) -> str:
        """Cheap title extraction for the pre-filter — iCIMS merges a
        JSON-LD blob with a listing row at fetch time. JSON-LD is the
        authoritative source; the listing row is the fallback. Mirrors
        ``normalize()`` so the filter never disagrees."""
        payload = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
        listing_row_any = payload.get("listing_row")
        jsonld_any = payload.get("jsonld")
        listing_row: dict[str, Any] = listing_row_any if isinstance(listing_row_any, dict) else {}
        jsonld: dict[str, Any] = jsonld_any if isinstance(jsonld_any, dict) else {}
        return str(jsonld.get("title") or listing_row.get("raw_title") or "")

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Map a merged iCIMS HTML + JSON-LD payload to NormalizedPosting."""
        payload = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
        listing_row = payload.get("listing_row") or {}
        if not isinstance(listing_row, dict):
            listing_row = {}
        jsonld = payload.get("jsonld") or {}
        if not isinstance(jsonld, dict):
            jsonld = {}
        careers_url = payload.get("careers_url") or ""
        if not isinstance(careers_url, str):
            careers_url = ""

        # ── Title ───────────────────────────────────────────────────────────
        raw_title = str(jsonld.get("title") or listing_row.get("raw_title") or "")

        # ── Location ────────────────────────────────────────────────────────
        location_raw = _extract_location_from_jsonld(jsonld)

        # ── JD body (HTML → plain text via shared helper) ───────────────────
        jd_html = jsonld.get("description") or ""
        jd_text = strip_html(str(jd_html)) if jd_html else ""

        # ── Locations + remote_type ─────────────────────────────────────────
        locations_normalized, derived_remote = parse_location(location_raw)
        # JSON-LD ``jobLocationType`` is the structured remote hint
        # ("TELECOMMUTE" → remote, "HYBRID" → hybrid). Some tenants put
        # "Remote" in the title or location instead — `parse_location`
        # picks that up on its own.
        loc_type = jsonld.get("jobLocationType")
        if isinstance(loc_type, str):
            lt = loc_type.upper()
            if "TELECOMMUTE" in lt or "REMOTE" in lt:
                remote_type = "remote"
            elif "HYBRID" in lt:
                remote_type = "hybrid"
            else:
                remote_type = derived_remote
        else:
            remote_type = derived_remote

        # ── Salary ──────────────────────────────────────────────────────────
        salary_min, salary_max, salary_currency = _extract_salary_from_jsonld(jsonld)
        # We don't reliably detect period; default to annual when a value
        # is present (the >=1000 threshold filters out hourly figures).
        salary_period = "annual" if (salary_min or salary_max) else "unknown"

        # ── Title-derived ───────────────────────────────────────────────────
        norm_title = normalize_title(raw_title)
        seniority = detect_seniority(norm_title)
        role_fam = detect_role_family(norm_title)

        # ── Department / team ───────────────────────────────────────────────
        department, team = _extract_department_team_from_jsonld(jsonld)

        # ── Timestamps ──────────────────────────────────────────────────────
        posted_at = _parse_iso_datetime(jsonld.get("datePosted"))

        # ── Source URL ──────────────────────────────────────────────────────
        # JSON-LD's ``url`` field is the canonical detail URL when present;
        # falls back to a constructed URL from the listing row.
        source_url = ""
        url_field = jsonld.get("url")
        if isinstance(url_field, str) and url_field.strip():
            source_url = url_field.strip()
        elif careers_url and listing_row.get("source_job_id"):
            source_url = _build_detail_url(
                careers_url,
                str(listing_row["source_job_id"]),
                str(listing_row.get("slug") or "job"),
            )

        with contextlib.suppress(Exception):
            # Same regression-spotting log Workday adapter uses for empty
            # JD bodies. If JSON-LD had a description string but our
            # strip_html removed all of it, something's off.
            if jd_html and not jd_text:
                logger.warning(
                    "icims.jd.empty_after_strip",
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
            ats="icims",
            source_job_id=raw.source_job_id,
            source_url=source_url,
            apply_url=source_url or None,
            raw_payload=raw.raw_payload,
            parser_version=self.parser_version,
        )
