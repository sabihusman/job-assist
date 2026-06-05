"""Fantastic.jobs (Apify) adapter — feat/fantastic-jobs-ingest.

Sources the curated Workday/iCIMS employers whose boards block Railway's
datacenter egress IP (Athene, Voya, EMC, Principal, Capital One, John Hancock).
Apify's infrastructure crawls those boards for us; we call the
``fantastic-jobs/career-site-job-listing-api`` actor's *run-sync-get-dataset-
items* endpoint (one synchronous request returns the matching jobs) and map the
results into the SAME ``NormalizedPosting`` shape the free adapters emit, so they
flow through the identical ingest path (content_hash dedupe → classifier →
scorer → hard rules).

ONE instance per employer: construct with the employer's organization name +
domain + real ATS; ``fetch_postings`` ignores the handle and queries Apify for
that one employer.

Title filter (API-side, the cost lever)
───────────────────────────────────────
The actor's ``titleSearch`` / ``titleExclusionSearch`` are PostgreSQL
full-text (tsquery) arrays — ``:*`` prefix only, NO phrase/AND/OR operators
documented, so a multi-word term matches as tokenized words. That means
``"Product Manager"`` ALSO matches "Senior Product Manager", "Group Product
Manager", etc. (and via stemming, "Product Management"). So titleSearch alone
can't isolate the operator's band (base + Associate PM/PO + **Senior PO only**).

The exclusion list does the narrowing, with one asymmetry to respect: you
CANNOT exclude bare ``"senior"`` — that would also drop the wanted **Senior
Product Owner**. So senior-PM is excluded as the multi-word ``"Senior Product
Manager"`` (tokens senior+product+manager) which drops Senior PM but leaves
Senior Product Owner untouched (it has no "manager" token). Every other
exclusion token (principal/group/director/head/…) is safe as a bare token
because none of the five wanted titles contain it. The first FILTERED run is
the empirical confirmation of this behaviour.

⚠️ The actor returns ``organization_url`` as the literal string
"Failed to construct 'URL': Invalid URL" on EVERY record — it's malformed. The
mapper NEVER reads it and strips it from the stored payload; the apply link is
the clean ``url`` field.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, ClassVar

import httpx

from job_assist.adapters.base import NormalizedPosting, RawPosting
from job_assist.adapters.normalization import (
    _sha256,
    compute_content_hash,
    detect_role_family,
    detect_seniority,
    normalize_title,
    parse_compensation,
)

# Apify actor + run-and-fetch endpoint (one POST runs the actor synchronously
# and returns the dataset items as a JSON array).
_ACTOR_ID = "fantastic-jobs~career-site-job-listing-api"
_RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{_ACTOR_ID}/run-sync-get-dataset-items"

# ── The locked PM/PO title filter (operator's early-to-mid band) ─────────────
# Wanted: Product Manager, Associate Product Manager, Product Owner,
#         Associate Product Owner, Senior Product Owner.
TITLE_SEARCH: list[str] = ["Product Manager", "Product Owner"]
TITLE_EXCLUSION_SEARCH: list[str] = [
    # Multi-word → drops Senior PM but KEEPS Senior Product Owner (no "manager").
    "Senior Product Manager",
    "Sr Product Manager",
    # Bare tokens — none of the five wanted titles contain these, so excluding
    # them can't drop a wanted role (and "senior" is deliberately NOT here).
    "Principal",
    "Group",
    "Director",
    "Head",
    "VP",
    "Vice President",
    "Chief",
    "Staff",
    "Lead",
    "Intern",
    # Wrong role types entirely.
    "Project Manager",
    "Program Manager",
    "Product Marketing",
]

# Per-employer result cap → the hard cost backstop. At $1.20 / 1,000 jobs,
# 50 jobs ≈ $0.06; with the PM/PO filter the real count is ~1-4/employer.
DEFAULT_LIMIT = 50


def build_actor_input(
    *,
    organization: str,
    domain: str | None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Build the actor input JSON: target one employer + the PM/PO title filter.

    Targets by ``domainFilter`` (exact, most reliable) when a domain is known,
    else falls back to ``organizationSearch`` (token match on the name).
    """
    body: dict[str, Any] = {
        "titleSearch": TITLE_SEARCH,
        "titleExclusionSearch": TITLE_EXCLUSION_SEARCH,
        "limit": limit,
    }
    if domain:
        body["domainFilter"] = [domain]
    else:
        body["organizationSearch"] = [organization]
    return body


def _source_job_id(rec: dict[str, Any]) -> str:
    """Stable per-posting id. Prefer the actor's ``id``; else hash the url."""
    rid = rec.get("id")
    if rid is not None and str(rid).strip():
        return str(rid).strip()
    url = rec.get("url")
    if isinstance(url, str) and url.strip():
        return _sha256(url.strip())[:32]
    return _sha256(f"{rec.get('organization', '')}|{rec.get('title', '')}")[:32]


def _parse_date(value: Any) -> datetime | None:
    """Best-effort ISO date/datetime → aware UTC datetime. None on anything odd."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:10])  # bare date
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_remote(rec: dict[str, Any]) -> bool:
    """Detect a remote role from the record's derived flags / location text."""
    for key in ("remote_derived", "is_remote", "remote"):
        if rec.get(key) is True:
            return True
    arrangement = rec.get("ai_work_arrangement") or rec.get("work_arrangement")
    return isinstance(arrangement, str) and "remote" in arrangement.lower()


def _map_geo(rec: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, str]:
    """Build (locations_normalized, location_raw, remote_type) from the DERIVED
    geo arrays (cleaner than the raw location string). Matches the shape
    ``parse_location`` emits: keys city/state/country/remote_type, plus a bare
    ``{"remote_type": "remote"}`` for remote."""
    cities = rec.get("cities_derived") or []
    regions = rec.get("regions_derived") or []
    countries = rec.get("countries_derived") or []
    entries: list[dict[str, Any]] = []
    raw_parts: list[str] = []

    if isinstance(cities, list):
        for i, city in enumerate(cities):
            if not isinstance(city, str) or not city.strip():
                continue
            c = city.strip()
            region = (
                regions[i].strip()
                if isinstance(regions, list)
                and i < len(regions)
                and isinstance(regions[i], str)
                and regions[i].strip()
                else None
            )
            country = (
                countries[i].strip()
                if isinstance(countries, list)
                and i < len(countries)
                and isinstance(countries[i], str)
                and countries[i].strip()
                else None
            )
            if c.lower() == "remote" or (region and region.lower() == "remote"):
                entries.append({"remote_type": "remote"})
                raw_parts.append("Remote")
            else:
                entries.append(
                    {"city": c, "state": region, "country": country, "remote_type": "onsite"}
                )
                raw_parts.append(f"{c}, {region}" if region else c)

    if _is_remote(rec) and not any(e.get("remote_type") == "remote" for e in entries):
        entries.append({"remote_type": "remote"})
        raw_parts.append("Remote")

    if any(e.get("remote_type") == "remote" for e in entries):
        remote_type = "remote"
    elif entries:
        remote_type = "onsite"
    else:
        remote_type = "unknown"

    location_raw = "; ".join(raw_parts) if raw_parts else None
    return entries, location_raw, remote_type


def map_record(
    rec: dict[str, Any],
    canonical_company_name: str,
    *,
    ats: str,
    source_job_id: str,
) -> NormalizedPosting:
    """Map one Apify job record → NormalizedPosting. Module-level (no network,
    no client) so it's unit-testable directly. NEVER reads ``organization_url``
    (the malformed "Failed to construct 'URL'…" field) and strips it from the
    stored payload."""
    raw_title = str(rec.get("title") or "")
    norm_title = normalize_title(raw_title)

    locations_normalized, location_raw, remote_type = _map_geo(rec)

    salary_min, salary_max, salary_currency, salary_period = parse_compensation(
        rec.get("salary_raw") if isinstance(rec.get("salary_raw"), str) else None
    )

    jd_text = str(rec.get("description_text") or "")
    posted_at = _parse_date(rec.get("date_posted"))

    # The clean apply link is ``url``. organization_url is the malformed
    # "Failed to construct 'URL'…" string on every record — NEVER used.
    url = str(rec.get("url") or "")
    safe_payload = {k: v for k, v in rec.items() if k != "organization_url"}

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
        salary_period=salary_period or "unknown",
        jd_text=jd_text,
        jd_text_hash=_sha256(jd_text),
        content_hash=compute_content_hash(canonical_company_name, norm_title, locations_normalized),
        posted_at=posted_at,
        first_seen_at=now,
        last_seen_at=now,
        seniority_level=detect_seniority(norm_title),
        role_family=detect_role_family(norm_title),
        department=None,
        team=None,
        ats=ats,
        source_job_id=source_job_id,
        source_url=url,
        apply_url=url or None,
        raw_payload=safe_payload,
        parser_version=FantasticJobsAdapter.parser_version,
    )


class FantasticJobsAdapter:
    """Adapter for one curated Workday/iCIMS employer, sourced via Apify.

    ``ats`` is an INSTANCE attribute (the employer's real ATS, ``workday`` or
    ``icims``) so the posting flows as a genuine Workday/iCIMS job; only the
    fetch path differs. ``parser_version`` records that it came via Apify.
    """

    parser_version: ClassVar[str] = "fantastic-v1"

    def __init__(
        self,
        *,
        organization: str,
        domain: str | None,
        ats: str,
        token: str,
        limit: int = DEFAULT_LIMIT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.organization = organization
        self.domain = domain
        self.ats = ats  # instance-level — the employer's real ATS (workday/icims)
        self._token = token
        self._limit = limit
        # run-sync can take a while (the actor runs end-to-end); generous timeout.
        self._client = client or httpx.AsyncClient(timeout=180.0)
        self._owns_client = client is None

    async def __aenter__(self) -> FantasticJobsAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        """Query Apify for this employer's PM/PO roles. ``handle`` is ignored —
        the employer is fixed at construction (organization/domain)."""
        if not self._token:
            raise RuntimeError("APIFY_API_TOKEN is not configured")
        body = build_actor_input(
            organization=self.organization, domain=self.domain, limit=self._limit
        )
        resp = await self._client.post(
            _RUN_SYNC_URL,
            json=body,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        data: Any = resp.json()
        records = data if isinstance(data, list) else []
        out: list[RawPosting] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            out.append(RawPosting(source_job_id=_source_job_id(rec), raw_payload=rec))
        return out

    def peek_title(self, raw: RawPosting) -> str:
        payload = raw.raw_payload
        return str(payload.get("title") or "") if isinstance(payload, dict) else ""

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        rec = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
        return map_record(
            rec, canonical_company_name, ats=self.ats, source_job_id=raw.source_job_id
        )
