"""Wellfound (ex-AngelList) ingest via the clearpath Apify actor.

feat/wellfound-ingest. Wellfound is a JOB BOARD, not a company board: we query
by ROLE (a ``wellfound.com/role/r/<slug>`` URL) and discover companies FROM the
returned postings — the inverse of the curated/fantastic company-keyed path.
So the Apify call lives here (one fetch per query), and the discovered records
are grouped by company in the service, which then drives each company's group
through the standard ``IngestionService.ingest_source`` pipeline via a thin
per-company wrapper. This reuses dedupe → classify → score → hard-rules → embed
unchanged; only the fetch + company-discovery is new.

Three Wellfound-specific guards live here:

  * **Hard cost caps** — every actor call is bounded by ``pageLimit`` AND a
    ``_MAX_RECORDS_PER_RUN`` failsafe. A filter regression must NEVER fail open
    into an unbounded paid fetch (the orgupdate lesson: one bad run = $20.88).
    A run is also cost-estimated and the ceiling trips an alert.
  * **Quality gate** — the wider Wellfound feed carries equity-only / co-founder
    / intern noise. A posting is kept only if it has a legitimacy badge (Top
    Investors / funding-stage) OR a real cash base salary at/above the floor.
  * **Transient retry** — the actor's ~80.8% run success means 1-in-5 fails;
    the fetch retries with backoff (the Gmail transient pattern) then soft-fails
    so a bad run never crashes the cron.
"""

from __future__ import annotations

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

from job_assist.adapters.base import NormalizedPosting, RawPosting
from job_assist.adapters.normalization import (
    _sha256,
    compute_content_hash,
    detect_role_family,
    detect_seniority,
    normalize_title,
)

logger = logging.getLogger(__name__)

# clearpath/wellfound-api-ppe — chosen in the actor evaluation (pay-per-result,
# no rental, monitorMode incremental, full JD text, 8-day-fresh validated).
_ACTOR_ID = "clearpath~wellfound-api-ppe"
_RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{_ACTOR_ID}/run-sync-get-dataset-items"

_ROLE_URL_TMPL = "https://wellfound.com/role/r/{role}"

# ── Hard cost caps ────────────────────────────────────────────────────────────
# pageLimit is the PRIMARY bound (one role page per run). _MAX_RECORDS_PER_RUN
# is the FAILSAFE: even if a filter regression returns a huge page, we truncate
# here and alert, so a single run can never bill past ~$0.87 (250 x base PPR).
_DEFAULT_PAGE_LIMIT = 1
_MAX_RECORDS_PER_RUN = 250
# clearpath base tier ≈ $3.49 / 1,000 results. Used only to ESTIMATE a run's
# charge for the cost-sanity guard / the Gate-1 readout — Apify bills the truth.
_PRICE_PER_RECORD_USD = 0.00349
# Abort/alert threshold: a single run estimated above this is anomalous.
_RUN_COST_ALERT_USD = 1.00

# ── Quality gate ──────────────────────────────────────────────────────────────
# Tunable. A posting with a real cash base salary at/above this floor is kept
# even without a legitimacy badge (the role/title noise is handled downstream by
# the classifier + hard rules; this gate only drops the equity-only/cofounder
# /intern chaff specific to the wider Wellfound feed).
_QUALITY_SALARY_FLOOR_USD = 100_000

PARSER_VERSION = "wellfound-v1"


def build_actor_input(
    *,
    role: str,
    only_remote: bool,
    page_limit: int = _DEFAULT_PAGE_LIMIT,
    monitor_mode: bool = False,
) -> dict[str, Any]:
    """Build the clearpath actor input for ONE role query.

    URL-driven (clearpath has no native keyword field): a role slug becomes a
    ``wellfound.com/role/r/<slug>`` search URL. ``pageLimit`` is clamped to the
    hard cap so a caller can never request an unbounded fetch.
    """
    return {
        "urls": [_ROLE_URL_TMPL.format(role=role)],
        "onlyRemoteJobs": only_remote,
        # Clamp defensively — the failsafe ceiling below is the real backstop,
        # but never let a caller pass pageLimit=0 (clearpath: 0 = unlimited).
        "pageLimit": max(1, min(int(page_limit), 5)),
        "monitorMode": monitor_mode,
        "sortBy": "LAST_POSTED",
    }


def _first(rec: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-None value among *keys* (defensive against
    the actor's field-name variance — confirmed/tuned against real records at
    the Gate-1 pull)."""
    for k in keys:
        v = rec.get(k)
        if v is not None:
            return v
    return None


def _company_of(rec: dict[str, Any]) -> dict[str, Any]:
    c = rec.get("company")
    return c if isinstance(c, dict) else {}


def company_name_of(rec: dict[str, Any]) -> str:
    """The posting's company name (used to group records + materialize shells)."""
    c = _company_of(rec)
    return str(_first(c, "name") or _first(rec, "company_name", "companyName") or "").strip()


def _base_salary(rec: dict[str, Any]) -> tuple[int | None, int | None, str]:
    """(min, max, currency) from ``compensation_parsed.base_salary`` — CASH only.

    Equity is deliberately NOT touched here: a 0.1-0.5% equity figure parsed as
    salary would wreck the salary-floor hard rule. Equity stays in raw_payload.
    """
    comp = rec.get("compensation_parsed")
    comp = comp if isinstance(comp, dict) else {}
    base = comp.get("base_salary")
    currency = str(_first(comp, "currency") or "USD")
    if isinstance(base, dict):
        lo = _coerce_int(_first(base, "min", "minimum", "low"))
        hi = _coerce_int(_first(base, "max", "maximum", "high"))
        currency = str(_first(base, "currency") or currency)
        return lo, (hi if hi is not None else lo), currency
    if isinstance(base, int | float) and base > 0:
        v = int(base)
        return v, v, currency
    return None, None, currency


def _coerce_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and float(v) > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    """Parse ``live_start_at`` (ISO) → aware UTC datetime; None on anything off."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _map_geo(rec: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, str]:
    """(locations_normalized, location_raw, remote_type) from Wellfound fields."""
    raw_locs = _first(rec, "locations", "location_names") or []
    locs = (
        [str(x).strip() for x in raw_locs if str(x).strip()] if isinstance(raw_locs, list) else []
    )
    location_raw = ", ".join(locs) if locs else None

    work_mode = str(_first(rec, "workMode", "work_mode", "remote_type") or "").lower()
    only_remote_flag = bool(_first(rec, "remote", "is_remote", "onlyRemoteJobs"))
    is_remote = (
        only_remote_flag or "remote" in work_mode or any("remote" in lo.lower() for lo in locs)
    )
    remote_type = "remote" if is_remote else ("onsite" if locs else "unknown")

    normalized = [{"raw": lo} for lo in locs]
    return normalized, location_raw, remote_type


def _seniority_from_years(years: Any) -> str | None:
    """Map ``years_experience_min`` → a seniority hint (secondary to title)."""
    n = _coerce_int(years)
    if n is None:
        return None
    if n >= 6:
        return "senior_pm"
    if n >= 3:
        return "pm"
    return "associate_pm"


def _has_legitimacy_badge(rec: dict[str, Any]) -> bool:
    """True when the record carries a trust signal — a Top-Investors / funding
    badge, or a non-empty funding stage. Tuned against real records at Gate 1."""
    if _first(rec, "funding_stage", "fundingStage"):
        return True
    c = _company_of(rec)
    if _first(c, "funding_stage", "fundingStage", "totalRaised", "total_raised"):
        return True
    badges = _first(rec, "badges") or _first(c, "badges") or []
    if isinstance(badges, list):
        joined = " ".join(str(b).lower() for b in badges)
        if "investor" in joined or "funding" in joined or "top" in joined:
            return True
    return bool(_first(rec, "top_investors", "topInvestors"))


def passes_quality_gate(rec: dict[str, Any]) -> bool:
    """Keep a posting iff it has a legitimacy badge OR a cash base salary whose
    MIN meets the floor. Drops the equity-only / co-founder / intern noise of
    the wider Wellfound feed. Pure + tunable; unit-tested directly."""
    if _has_legitimacy_badge(rec):
        return True
    lo, _hi, _cur = _base_salary(rec)
    return lo is not None and lo >= _QUALITY_SALARY_FLOOR_USD


def map_wellfound_record(
    rec: dict[str, Any],
    canonical_company_name: str,
    *,
    source_job_id: str,
) -> NormalizedPosting:
    """Map one clearpath Wellfound record → NormalizedPosting. Module-level (no
    network) so it's unit-testable. Equity is left in raw_payload only — never
    mapped to a salary field."""
    raw_title = str(_first(rec, "title", "role_title") or "")
    norm_title = normalize_title(raw_title)

    locations_normalized, location_raw, remote_type = _map_geo(rec)
    salary_min, salary_max, salary_currency = _base_salary(rec)
    jd_text = str(_first(rec, "description", "description_text", "jd_text", "jd") or "")
    url = str(_first(rec, "url", "apply_url", "applyUrl") or "")
    posted_at = _parse_dt(_first(rec, "live_start_at", "liveStartAt", "posted_at", "postedAt"))

    # Title detection is primary; years_experience_min only fills an 'unknown'.
    seniority = detect_seniority(norm_title)
    if seniority == "unknown":
        seniority = (
            _seniority_from_years(_first(rec, "years_experience_min", "yearsExperienceMin"))
            or seniority
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
        salary_currency=salary_currency if salary_min is not None else None,
        salary_period="annual" if salary_min is not None else "unknown",
        jd_text=jd_text,
        jd_text_hash=_sha256(jd_text),
        content_hash=compute_content_hash(canonical_company_name, norm_title, locations_normalized),
        posted_at=posted_at,
        first_seen_at=now,
        last_seen_at=now,
        seniority_level=seniority,
        role_family=detect_role_family(norm_title),
        department=None,
        team=None,
        ats="wellfound",
        source_job_id=source_job_id,
        source_url=url,
        apply_url=url or None,
        raw_payload=rec,
        parser_version=PARSER_VERSION,
    )


def _source_job_id(rec: dict[str, Any]) -> str:
    """Stable per-posting id — Wellfound's ``id``, else hash the url."""
    jid = _first(rec, "id", "jobId", "job_id")
    if jid:
        return str(jid)
    return _sha256(str(_first(rec, "url") or ""))[:24]


class WellfoundFetchError(RuntimeError):
    """The actor run failed (non-2xx / transport) after retries — soft-failed."""


class CostGuardTripped(RuntimeError):
    """A single run's estimated charge exceeded the alert ceiling."""


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
async def _run_actor(client: httpx.AsyncClient, token: str, body: dict[str, Any]) -> list[Any]:
    """One actor run with transient-retry (the ~80.8%-success backstop)."""
    resp = await client.post(_RUN_SYNC_URL, json=body, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    data: Any = resp.json()
    return data if isinstance(data, list) else []


class WellfoundQuery:
    """Runs ONE role query against the actor and yields quality-passing
    RawPostings. Holds the run's cost/skip telemetry for the service to report.
    """

    def __init__(
        self,
        *,
        token: str,
        role: str,
        only_remote: bool = True,
        page_limit: int = _DEFAULT_PAGE_LIMIT,
        monitor_mode: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._role = role
        self._only_remote = only_remote
        self._page_limit = page_limit
        self._monitor_mode = monitor_mode
        self._client = client or httpx.AsyncClient(timeout=180.0)
        self._owns_client = client is None
        # Telemetry (read by the service for the Gate-1 readout).
        self.fetched = 0
        self.kept = 0
        self.skipped_quality = 0
        self.estimated_cost_usd = 0.0
        self.cost_guard_tripped = False

    async def __aenter__(self) -> WellfoundQuery:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def run(self) -> list[RawPosting]:
        if not self._token:
            raise RuntimeError("APIFY_API_TOKEN is not configured")
        body = build_actor_input(
            role=self._role,
            only_remote=self._only_remote,
            page_limit=self._page_limit,
            monitor_mode=self._monitor_mode,
        )
        try:
            records = await _run_actor(self._client, self._token, body)
        except httpx.HTTPError as exc:
            raise WellfoundFetchError(f"Wellfound actor run failed: {exc}") from exc

        records = [r for r in records if isinstance(r, dict)]
        self.fetched = len(records)

        # ── Cost failsafe ────────────────────────────────────────────────────
        # Truncate a runaway page BEFORE counting cost, then alert. The actor
        # already billed for what it returned, but truncation caps our DOWNSTREAM
        # work and the log makes the regression visible immediately.
        if len(records) > _MAX_RECORDS_PER_RUN:
            self.cost_guard_tripped = True
            logger.warning(
                "wellfound.cost_guard_tripped: actor returned %d records (cap %d) "
                "for role=%s — truncating. Check the query/filters.",
                len(records),
                _MAX_RECORDS_PER_RUN,
                self._role,
            )
            records = records[:_MAX_RECORDS_PER_RUN]

        self.estimated_cost_usd = round(self.fetched * _PRICE_PER_RECORD_USD, 4)
        if self.estimated_cost_usd > _RUN_COST_ALERT_USD:
            self.cost_guard_tripped = True
            logger.warning(
                "wellfound.cost_alert: run estimated at $%.2f (ceiling $%.2f) for role=%s",
                self.estimated_cost_usd,
                _RUN_COST_ALERT_USD,
                self._role,
            )

        out: list[RawPosting] = []
        for rec in records:
            if not passes_quality_gate(rec):
                self.skipped_quality += 1
                continue
            out.append(RawPosting(source_job_id=_source_job_id(rec), raw_payload=rec))
        self.kept = len(out)
        return out


class PrefetchedCompanyAdapter:
    """A thin :class:`Adapter` over ONE company's already-fetched Wellfound
    records, so the service can drive each discovered company through the
    standard ``ingest_source`` pipeline. No network — the Apify call already
    happened in :class:`WellfoundQuery`."""

    ats: ClassVar[str] = "wellfound"
    parser_version: ClassVar[str] = PARSER_VERSION

    def __init__(self, records: list[RawPosting]) -> None:
        self._records = records

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        return list(self._records)

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        rec = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
        return map_wellfound_record(rec, canonical_company_name, source_job_id=raw.source_job_id)

    def peek_title(self, raw: RawPosting) -> str:
        rec = raw.raw_payload if isinstance(raw.raw_payload, dict) else {}
        return str(_first(rec, "title", "role_title") or "")

    async def __aenter__(self) -> PrefetchedCompanyAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


__all__ = [
    "CostGuardTripped",
    "PrefetchedCompanyAdapter",
    "WellfoundFetchError",
    "WellfoundQuery",
    "build_actor_input",
    "company_name_of",
    "map_wellfound_record",
    "passes_quality_gate",
]
