"""Unit tests for the Wellfound adapter (feat/wellfound-ingest).

Pure mapper / quality-gate / cost-cap / retry coverage — no DB. The actor HTTP
surface is mocked with httpx fakes (same pattern as the Gmail/fantastic suites).
Field names mirror the operator-validated live sample
(``compensation_parsed.base_salary``, ``equity``, ``live_start_at``,
``years_experience_min``, ``id``, ``url``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from job_assist.adapters.wellfound import (
    _MAX_RECORDS_PER_RUN,
    _QUALITY_SALARY_FLOOR_USD,
    WellfoundFetchError,
    WellfoundQuery,
    build_actor_input,
    company_name_of,
    map_wellfound_record,
    passes_quality_gate,
)


def _rec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "2543210",
        "title": "Senior Product Manager",
        "url": "https://wellfound.com/jobs/2543210",
        "description": "We're building the future of payments. Own the roadmap...",
        "live_start_at": "2026-06-04T12:00:00Z",
        "years_experience_min": 6,
        "compensation_parsed": {"base_salary": {"min": 150000, "max": 190000, "currency": "USD"}},
        "equity": {"min": 0.1, "max": 0.5},
        "locations": ["Remote (US)"],
        "remote": True,
        "company": {"name": "Acme Labs", "slug": "acme-labs", "fundingStage": "Series A"},
    }
    base.update(over)
    return base


# ── build_actor_input: hard cost caps ─────────────────────────────────────────


def test_build_actor_input_role_url_and_remote() -> None:
    body = build_actor_input(role="product-manager", only_remote=True)
    assert body["urls"] == ["https://wellfound.com/role/r/product-manager"]
    assert body["onlyRemoteJobs"] is True
    assert body["sortBy"] == "LAST_POSTED"


def test_build_actor_input_clamps_page_limit_never_unbounded() -> None:
    # clearpath treats pageLimit=0 as UNLIMITED — the clamp must forbid it, and
    # cap the upper bound so a caller can't fan out a huge paid fetch.
    assert (
        build_actor_input(role="product-manager", only_remote=True, page_limit=0)["pageLimit"] == 1
    )
    assert (
        build_actor_input(role="product-manager", only_remote=True, page_limit=99)["pageLimit"] == 5
    )


# ── Quality gate ──────────────────────────────────────────────────────────────


def test_quality_gate_keeps_funding_badge() -> None:
    # Funding stage present, no salary → kept (legitimacy badge).
    assert passes_quality_gate(
        _rec(compensation_parsed={}, company={"name": "X", "fundingStage": "Seed"})
    )


def test_quality_gate_keeps_salary_at_floor() -> None:
    rec = _rec(
        company={"name": "X"},  # no funding badge
        compensation_parsed={"base_salary": {"min": _QUALITY_SALARY_FLOOR_USD, "max": 200000}},
    )
    assert passes_quality_gate(rec)


def test_quality_gate_drops_equity_only_below_floor_no_badge() -> None:
    # Equity-only / co-founder noise: no badge, salary under floor (or absent).
    rec = _rec(
        company={"name": "Tiny Startup"},  # no fundingStage
        compensation_parsed={"base_salary": {"min": 60000, "max": 80000}},
        equity={"min": 1.0, "max": 2.0},
    )
    assert passes_quality_gate(rec) is False


def test_quality_gate_drops_no_salary_no_badge() -> None:
    assert passes_quality_gate(_rec(compensation_parsed={}, company={"name": "Tiny"})) is False


# ── Mapper: field mapping + equity isolation ──────────────────────────────────


def test_map_record_core_fields() -> None:
    np = map_wellfound_record(_rec(), "Acme Labs", source_job_id="2543210")
    assert np.raw_title == "Senior Product Manager"
    assert np.canonical_company_name == "Acme Labs"
    assert np.ats == "wellfound"
    assert np.source_job_id == "2543210"
    assert np.source_url == "https://wellfound.com/jobs/2543210"
    assert np.apply_url == "https://wellfound.com/jobs/2543210"
    assert np.jd_text.startswith("We're building")
    assert np.remote_type == "remote"
    assert np.posted_at is not None and np.posted_at.year == 2026


def test_map_record_base_salary_only_equity_stays_in_payload() -> None:
    np = map_wellfound_record(_rec(), "Acme Labs", source_job_id="2543210")
    # CASH base salary → salary columns.
    assert np.salary_min == 150000
    assert np.salary_max == 190000
    assert np.salary_currency == "USD"
    assert np.salary_period == "annual"
    # Equity is NEVER mapped to a salary field — it survives only in raw_payload.
    assert "equity" in np.raw_payload
    assert np.raw_payload["equity"] == {"min": 0.1, "max": 0.5}


def test_map_record_no_salary_leaves_salary_null() -> None:
    np = map_wellfound_record(_rec(compensation_parsed={}), "Acme Labs", source_job_id="x")
    assert np.salary_min is None
    assert np.salary_max is None
    assert np.salary_currency is None
    assert np.salary_period == "unknown"


def test_map_record_years_experience_fills_unknown_seniority() -> None:
    # A title with no seniority signal + years_experience_min=6 → senior hint.
    np = map_wellfound_record(
        _rec(title="Product Manager", years_experience_min=6),
        "Acme Labs",
        source_job_id="x",
    )
    assert np.seniority_level in {"senior_pm", "pm", "associate_pm"}


def test_company_name_of_reads_nested_company() -> None:
    assert company_name_of(_rec()) == "Acme Labs"
    assert company_name_of({"company_name": "Flat Co"}) == "Flat Co"
    assert company_name_of({}) == ""


# ── content_hash cross-source dedupe alignment ───────────────────────────────


def test_content_hash_matches_when_company_title_locations_align() -> None:
    # The Wellfound copy and a Greenhouse copy of the SAME role must share a
    # content_hash so they dedupe to one JobPosting — given the SAME canonical
    # company name (the service resolves to the existing target_company's name).
    wf = map_wellfound_record(
        _rec(title="Senior Product Manager", locations=["Remote (US)"]),
        "Stripe",
        source_job_id="wf-1",
    )
    # Simulate the Greenhouse path producing the same normalized inputs.
    from job_assist.adapters.normalization import compute_content_hash, normalize_title

    gh_hash = compute_content_hash(
        "Stripe", normalize_title("Senior Product Manager"), [{"raw": "Remote (US)"}]
    )
    assert wf.content_hash == gh_hash


# ── WellfoundQuery: cost cap + retry + soft-fail (mocked actor) ───────────────


class _FakeResp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.calls = 0

    async def post(self, *_a: Any, **_k: Any) -> Any:
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_query_quality_filters_and_counts() -> None:
    keep = _rec(id="1")
    drop = _rec(id="2", compensation_parsed={}, company={"name": "Tiny"})  # fails gate
    client = _FakeClient([_FakeResp([keep, drop])])
    q = WellfoundQuery(token="t", role="product-manager", client=client)  # type: ignore[arg-type]
    raws = await q.run()
    assert q.fetched == 2
    assert q.kept == 1 and len(raws) == 1
    assert q.skipped_quality == 1
    assert q.estimated_cost_usd > 0


@pytest.mark.asyncio
async def test_query_cost_guard_truncates_runaway_page() -> None:
    # A filter regression returns a huge page → truncate to the failsafe cap +
    # trip the guard (the "one bad run = $20.88" backstop).
    huge = [_rec(id=str(i)) for i in range(_MAX_RECORDS_PER_RUN + 50)]
    client = _FakeClient([_FakeResp(huge)])
    q = WellfoundQuery(token="t", role="product-manager", client=client)  # type: ignore[arg-type]
    raws = await q.run()
    assert q.cost_guard_tripped is True
    assert len(raws) <= _MAX_RECORDS_PER_RUN


@pytest.mark.asyncio
async def test_query_retries_transient_then_succeeds() -> None:
    # First call raises a transient HTTP error; tenacity retries → success.
    client = _FakeClient([httpx.ConnectError("flaky"), _FakeResp([_rec(id="1")])])
    q = WellfoundQuery(token="t", role="product-manager", client=client)  # type: ignore[arg-type]
    raws = await q.run()
    assert client.calls == 2
    assert len(raws) == 1


@pytest.mark.asyncio
async def test_query_soft_fails_after_exhausted_retries() -> None:
    client = _FakeClient([httpx.ConnectError("down")])  # always fails
    q = WellfoundQuery(token="t", role="product-manager", client=client)  # type: ignore[arg-type]
    with pytest.raises(WellfoundFetchError):
        await q.run()


@pytest.mark.asyncio
async def test_query_empty_token_raises() -> None:
    q = WellfoundQuery(token="", role="product-manager", client=_FakeClient([]))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="APIFY_API_TOKEN"):
        await q.run()
