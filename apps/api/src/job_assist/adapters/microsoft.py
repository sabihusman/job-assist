"""Microsoft Careers native adapter (directive B).

Microsoft's careers site is NOT a standard ATS board — ``careers.microsoft.com``
is an AEM marketing shell that redirects into a separate app host,
``apply.careers.microsoft.com``, which serves a custom JSON API (namespace
``pcsx_job_search``). Confirmed via a live network trace (Phase 1, this PR):

  * List/search: ``GET /api/pcsx/search?domain=microsoft.com&query=<term>
    &location=&start=<offset>&`` — fixed page size of 10, ``data.count`` is
    the total match count, ``start`` is a plain numeric offset (no cursor).
    ``/api/suggest`` is pure typeahead, NOT the list endpoint — do not use it.
  * Detail: ``GET /api/pcsx/position_details?position_id=<id>&domain=
    microsoft.com&hl=en`` — full posting incl. ``jobDescription`` (HTML).
  * No auth/cookies required — both endpoints returned clean 200s with
    ``credentials: 'omit'``. Safe for a scheduled Railway job.
  * No structured seniority/level field exists anywhere in the payload —
    level lives only in the title text (and a free-text comp blurb inside
    the description, e.g. "Product Management IC5"). Seniority/role-family
    are therefore derived the same way every other adapter does it: from
    the normalized title, via ``normalization.detect_seniority`` /
    ``detect_role_family``.

Title matching
--------------
The free-text ``query`` param does fuzzy/relevance search, not literal title
filtering — almost no live posting is a *bare* "Product Manager" / "Technical
Program Manager" / "Program Manager", and a plain substring match on "program
manager" pulls in a lot of postings that aren't product/tech program
management at all (Data Center, Compliance, Supply Chain, Sales Operations,
Legal Operations, Business Operations program managers, all observed live).
Confirmed with the operator (Phase 1 sign-off): use an allow-list + exclusion
strategy — keep any seniority-qualified variant of the four target titles,
explicitly excluding the observed non-target department flavors. The
downstream Gemini classifier remains the precision pass for anything that
still slips through, matching this repo's existing over-inclusion philosophy
(see ``adapters/title_filter.py``).

The "Product Manager 1" seed doesn't need special-case matching — any title
containing "product manager" (including a hypothetical "Product Manager 1"/
"Product Manager I") is already covered by the product-manager allow-list
below. Zero results specifically for that seed is expected, not a bug —
Microsoft doesn't appear to have any open req at that literal title today.

Rate limiting
-------------
There's no client-side token-bucket precedent in this codebase — other
adapters make one call per ``fetch_postings()`` invocation and rely on the
cron-layer ``THROTTLE_SECONDS`` gap between calls. This adapter makes many
calls in a single invocation (paginating four seed queries, then fetching
details), so it rate-limits itself: confirmed with the operator at 1
request/second, with pagination stopped early once a query's last
``_MAX_CONSECUTIVE_EMPTY_PAGES`` pages produced zero title-filter keeps
(most real PM/TPM postings are dense on early pages; walking deep into a
749-hit noisy query for a handful of stragglers isn't worth the request
budget). ``_MAX_START_PER_QUERY`` is a hard backstop in case that heuristic
never fires.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
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
    parse_compensation,
    parse_location,
    strip_html,
)
from job_assist.adapters.title_filter import should_keep_title

logger = logging.getLogger(__name__)

_BASE_URL = "https://apply.careers.microsoft.com"
_SEARCH_URL = f"{_BASE_URL}/api/pcsx/search"
_DETAIL_URL = f"{_BASE_URL}/api/pcsx/position_details"
_APPLY_URL_TMPL = f"{_BASE_URL}/careers/apply?pid={{position_id}}&domain=microsoft.com"

_PAGE_SIZE = 10
_SEED_QUERIES: tuple[str, ...] = (
    "product manager",
    "technical program manager",
    "program manager",
    "product manager 1",
)
_MAX_CONSECUTIVE_EMPTY_PAGES = 3
_MAX_START_PER_QUERY = 500  # backstop only; the empty-page heuristic should fire first
_DEFAULT_RATE_LIMIT_SECONDS = 1.0

_REMOTE_TYPE_MAP = {"onsite": "onsite", "hybrid": "hybrid", "remote": "remote"}

# ── Title keep-list: program-manager family ──────────────────────────────────
#
# The product-manager family reuses ``title_filter.should_keep_title`` (track
# "pm") unchanged — it already handles seniority prefixes and the
# marketing/design/engineering/support exclusions. Program management has no
# equivalent existing filter in this codebase (should_keep_title's "pm" track
# only matches "product"-flavored titles), so it's built here, local to this
# adapter, rather than extending the shared module for a family only this
# source cares about right now.
_PROGRAM_ROLE_RE = re.compile(r"\bprogram\s+manag(?:er|ement)\b", re.IGNORECASE)

# Observed live on careers.microsoft.com (Phase 1 trace) as non-target
# "Program Manager" flavors — not exhaustive by design; the classifier is the
# precision pass for whatever slips through (same philosophy as title_filter).
_PROGRAM_ROLE_EXCLUSIONS: tuple[str, ...] = (
    "data center",
    "datacenter",
    "data centre",
    "critical environment",
    "supply chain",
    "sales operations",
    "sales ops",
    "legal operations",
    "legal ops",
    "business operations",
    # Microsoft's own career_discipline facet lists "Business Program
    # Management" as a discipline distinct from "Technical Program
    # Management" / "Product Management" — a separate PM-adjacent-but-not-
    # target function (process/operations program work), not product/tech PM.
    "business program",
    "compliance",
    "construction",
    "facilities",
    "real estate",
    "accounting",
    "manufacturing",
    "human resources",
    "recruiting",
    "talent acquisition",
    "finance",
)


def _should_keep_msft_title(raw_title: str | None) -> bool:
    """True iff *raw_title* belongs to one of the four target title families.

    Two independent paths to "yes": the existing product-manager keep-list
    (unchanged, reused as-is), or the program-manager allow-list defined
    above (with its own exclusion list — a title can be excluded from the
    program-manager path while still being caught by the product-manager
    path, e.g. "Product Manager, Business Operations" stays IN).
    """
    if not raw_title or not raw_title.strip():
        return False
    if should_keep_title(raw_title, track="pm"):
        return True
    lowered = raw_title.lower()
    if not _PROGRAM_ROLE_RE.search(lowered):
        return False
    return not any(phrase in lowered for phrase in _PROGRAM_ROLE_EXCLUSIONS)


class MicrosoftCareersAdapter:
    """Adapter for Microsoft's careers site (``apply.careers.microsoft.com``).

    Unlike Greenhouse/Lever/Ashby, Microsoft isn't a per-company board keyed
    by a handle we look up — it's a single tenant (Microsoft itself) with a
    query-driven search API. ``fetch_postings(handle)`` still accepts
    *handle* to satisfy the ``Adapter`` protocol (and so ``target_company``
    seeding / the existing ingest-plan machinery works unchanged), but the
    adapter ignores its value — the search domain is fixed to
    ``microsoft.com``.
    """

    ats: ClassVar[str] = "microsoft"
    parser_version: ClassVar[str] = "microsoft-v1"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        rate_limit_seconds: float = _DEFAULT_RATE_LIMIT_SECONDS,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=60.0, follow_redirects=True, headers=BROWSER_HEADERS
        )
        self._owns_client = client is None
        self._rate_limit_seconds = rate_limit_seconds

    async def __aenter__(self) -> MicrosoftCareersAdapter:
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
    async def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        resp = await self._client.get(url, params=params)
        if resp.status_code >= 500:
            resp.raise_for_status()
        if self._rate_limit_seconds > 0:
            await asyncio.sleep(self._rate_limit_seconds)
        return resp

    async def _search_page(self, query: str, start: int) -> httpx.Response:
        return await self._get(
            _SEARCH_URL,
            {"domain": "microsoft.com", "query": query, "location": "", "start": start},
        )

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        """Paginate the four seed queries, title-filter, dedupe, then fetch
        details for the survivors.

        Bestiary 5.9: only the FIRST search call (first seed query, start=0)
        is treated as the "listing-level" call — a 404 there raises
        ``HandleNotFoundError``. A 404/non-200 on a later page just ends
        that query's pagination (matches every other adapter's "non-200 on
        a non-listing call → treat as end of data" behavior).

        Bestiary 5.19: a retry-exhausted timeout/HTTPError from ``_get``
        PROPAGATES — it is NOT swallowed. Same rationale as every other
        adapter: an empty list on a transient network failure is
        indistinguishable from "no postings today" and would falsely close
        every live Microsoft posting via stale-detection.
        """
        candidates: dict[int, str] = {}

        for query_index, query in enumerate(_SEED_QUERIES):
            start = 0
            consecutive_empty = 0
            while start < _MAX_START_PER_QUERY:
                resp = await self._search_page(query, start)
                if resp.status_code == 404:
                    if query_index == 0 and start == 0:
                        raise HandleNotFoundError(
                            ats=self.ats, handle=handle, url=str(resp.request.url)
                        )
                    break
                if resp.status_code != 200:
                    break
                payload: Any = resp.json()
                if not isinstance(payload, dict):
                    break
                data = payload.get("data")
                positions = data.get("positions") if isinstance(data, dict) else None
                if not isinstance(positions, list) or not positions:
                    break

                page_keeps = 0
                for pos in positions:
                    if not isinstance(pos, dict):
                        continue
                    pos_id = pos.get("id")
                    title = pos.get("name")
                    if pos_id is None or not title:
                        continue
                    if _should_keep_msft_title(str(title)):
                        candidates.setdefault(int(pos_id), str(title))
                        page_keeps += 1

                consecutive_empty = 0 if page_keeps else consecutive_empty + 1
                if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY_PAGES:
                    break
                if len(positions) < _PAGE_SIZE:
                    break
                start += _PAGE_SIZE
            else:
                logger.warning(
                    "microsoft.pagination.cap_hit",
                    extra={"query": query, "max_start": _MAX_START_PER_QUERY},
                )

        out: list[RawPosting] = []
        for pos_id in candidates:
            resp = await self._get(
                _DETAIL_URL, {"position_id": pos_id, "domain": "microsoft.com", "hl": "en"}
            )
            if resp.status_code != 200:
                # Per-job 404 (deleted between listing and detail) or a
                # transient non-200 — silent skip, matching the Bestiary 5.9
                # per-job convention (only the FIRST listing call raises).
                continue
            detail_payload: Any = resp.json()
            if not isinstance(detail_payload, dict):
                continue
            detail = detail_payload.get("data")
            if not isinstance(detail, dict):
                continue
            out.append(RawPosting(source_job_id=str(pos_id), raw_payload=detail))
        return out

    def peek_title(self, raw: RawPosting) -> str:
        """Cheap title extraction for the pre-filter — mirrors ``normalize()``'s
        extraction so the filter sees the same string the rest of the pipeline
        will see."""
        return str(raw.raw_payload.get("name") or "")

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        """Convert one ``position_details`` payload to a NormalizedPosting."""
        data = raw.raw_payload
        raw_title = str(data.get("name") or "")
        norm_title = normalize_title(raw_title)

        locations = data.get("standardizedLocations") or data.get("locations") or []
        location_raw = " / ".join(str(loc) for loc in locations) if locations else None
        locations_normalized, derived_remote = parse_location(location_raw)

        work_location_option = str(data.get("workLocationOption") or "").lower()
        remote_type = _REMOTE_TYPE_MAP.get(work_location_option, derived_remote)

        jd_text = strip_html(str(data.get("jobDescription") or ""))

        # Microsoft has no structured comp field — the pay band (when present)
        # is free text inside the description ("...IC5 - $142,800 - $274,800
        # per year..."). parse_compensation is built for exactly this: it
        # scans a whole JD body and picks the best USD range candidate.
        salary_min, salary_max, salary_currency, salary_period_str = parse_compensation(jd_text)
        salary_period = salary_period_str or "unknown"

        posted_at: datetime | None = None
        posted_ts = data.get("postedTs")
        if isinstance(posted_ts, int | float) and posted_ts > 0:
            with contextlib.suppress(ValueError, OSError, OverflowError):
                posted_at = datetime.fromtimestamp(posted_ts, tz=UTC)

        department = normalize_org_field(data.get("department"))

        position_id = data.get("id") or raw.source_job_id
        position_url = data.get("positionUrl")
        source_url = str(
            data.get("publicUrl") or (f"{_BASE_URL}{position_url}" if position_url else "")
        )
        apply_url = _APPLY_URL_TMPL.format(position_id=position_id)

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
            seniority_level=detect_seniority(norm_title),
            role_family=detect_role_family(norm_title),
            department=department,
            team=None,
            ats=self.ats,
            source_job_id=raw.source_job_id,
            source_url=source_url,
            apply_url=apply_url,
            raw_payload=raw.raw_payload,
            parser_version=self.parser_version,
        )
