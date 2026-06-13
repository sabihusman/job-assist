"""Unit tests for the Wellfound adapter (feat/wellfound-ingest).

Pure mapper / quality-gate / cost-cap / retry coverage — no DB. The actor HTTP
surface is mocked with httpx fakes (same pattern as the Gmail/fantastic suites).
``_REAL_RECORD`` pins the Gate-1-confirmed clearpath shape:
``compensation_parsed.base_salary.{min_value,max_value,currency,unit}``,
``company_badges`` (array), ``live_start_at`` (unix epoch), ``equity_parsed``,
``company_name`` (flat), ``location_names``, ``remote``.
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

# The REAL clearpath record shape (Gate-1-confirmed field paths). Pinned as the
# canonical fixture: a future actor-schema change that breaks these keys must
# fail THIS test, not the silent prod quality gate (which dropped 43/43 on the
# first Gate-1 pull because the assumed paths were wrong).
_REAL_RECORD: dict[str, Any] = {
    "id": "2543210",
    "title": "Senior Product Manager",
    "company_name": "Transfix",
    "company_slug": "transfix",
    "company_size": "SIZE_51_200",
    "company_badges": ["Scale Stage", "Top Investors", "Actively Hiring"],
    "url": "https://wellfound.com/jobs/2543210",
    "description": "We're building the future of freight. Own the roadmap end to end.",
    "live_start_at": 1780574400,  # unix epoch (2026-06-04), NOT ISO
    "live_start_at_days_ago": 8,
    "years_experience_min": 6,
    "compensation_parsed": {
        "base_salary": {
            "min_value": 150000,
            "max_value": 190000,
            "currency": "USD",
            "unit": "YEARLY",
        },
    },
    "equity": None,
    "equity_parsed": {"min_value": 0.1, "max_value": 0.5},
    "location_names": ["Remote (US)"],
    "remote": True,
    "monitor_status": "NEW",
}


def _rec(**over: Any) -> dict[str, Any]:
    import copy

    base = copy.deepcopy(_REAL_RECORD)
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
    # A funding/investor company_badge keeps it even with no salary. The default
    # fixture's "Scale Stage" / "Top Investors" badges are the real signal.
    assert passes_quality_gate(
        _rec(company_badges=["Top Investors", "Actively Hiring"], compensation_parsed={})
    )


def test_quality_gate_keeps_salary_at_floor() -> None:
    # No legitimacy badge ("Actively Hiring" alone is no signal), but a cash
    # base salary at the floor → kept.
    rec = _rec(
        company_badges=["Actively Hiring"],
        compensation_parsed={
            "base_salary": {"min_value": _QUALITY_SALARY_FLOOR_USD, "max_value": 200000}
        },
    )
    assert passes_quality_gate(rec)


def test_quality_gate_drops_equity_only_below_floor_no_badge() -> None:
    # Equity-only / co-founder noise: no funding badge, salary under floor.
    rec = _rec(
        company_badges=["Actively Hiring"],
        compensation_parsed={"base_salary": {"min_value": 60000, "max_value": 80000}},
        equity_parsed={"min_value": 1.0, "max_value": 2.0},
    )
    assert passes_quality_gate(rec) is False


def test_quality_gate_drops_no_salary_no_badge() -> None:
    assert (
        passes_quality_gate(_rec(company_badges=["Actively Hiring"], compensation_parsed={}))
        is False
    )


def test_actively_hiring_alone_is_not_a_legitimacy_signal() -> None:
    # Every live post carries "Actively Hiring" — it must NOT pass the gate on
    # its own, else the gate would keep everything (the inverse of the 43/43
    # drop bug).
    assert passes_quality_gate(
        _rec(company_badges=["Actively Hiring"], compensation_parsed={})
    ) is (False)


# ── Mapper: field mapping + equity isolation ──────────────────────────────────


def test_map_real_record_end_to_end() -> None:
    """The PINNED real clearpath record through the mapper. If a future actor
    schema change moves any of these keys, THIS fails — not the silent prod
    quality gate (which dropped 43/43 at the first Gate-1 pull)."""
    np = map_wellfound_record(_REAL_RECORD, "Transfix", source_job_id="2543210")
    assert np.raw_title == "Senior Product Manager"
    assert np.ats == "wellfound"
    assert np.source_job_id == "2543210"
    assert np.source_url == "https://wellfound.com/jobs/2543210"
    assert np.jd_text.startswith("We're building")
    assert np.remote_type == "remote"
    # min_value/max_value/unit (not min/max) → salary columns.
    assert (np.salary_min, np.salary_max, np.salary_currency) == (150000, 190000, "USD")
    assert np.salary_period == "annual"
    # live_start_at is a UNIX EPOCH INT, not ISO — must parse to 2026-06.
    assert np.posted_at is not None
    assert np.posted_at.year == 2026 and np.posted_at.month == 6
    # The real record passes the gate (Scale Stage / Top Investors badges).
    assert passes_quality_gate(_REAL_RECORD) is True


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
    # live_start_at unix epoch → 2026 (the ISO parser would have failed it).
    assert np.posted_at is not None and np.posted_at.year == 2026


def test_map_record_base_salary_only_equity_stays_in_payload() -> None:
    np = map_wellfound_record(_rec(), "Acme Labs", source_job_id="2543210")
    # CASH base salary (min_value/max_value, unit=YEARLY) → salary columns.
    assert np.salary_min == 150000
    assert np.salary_max == 190000
    assert np.salary_currency == "USD"
    assert np.salary_period == "annual"
    # Equity is NEVER mapped to a salary field — it survives only in raw_payload
    # (under equity_parsed, exactly as the actor returned it).
    assert np.raw_payload["equity_parsed"] == {"min_value": 0.1, "max_value": 0.5}
    assert np.raw_payload.get("equity") is None


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
    assert np.seniority_level in {"senior_pm", "pm", "apm"}


# Regression guard for the asyncpg InvalidTextRepresentationError that failed
# every low-years Wellfound INSERT in prod: _seniority_from_years returned
# "associate_pm", which is NOT a member of the seniority_level enum (the member
# is "apm"). The seniority string is written to the column pre-classification,
# so an off-enum value fails the whole ingest_run. Assert EVERY branch maps to a
# real SeniorityLevel member — exercising the <3 branch the old test skipped.
@pytest.mark.parametrize(
    ("years", "expected"),
    [
        # years<=0 → None (_coerce_int guards float(v) > 0): "unspecified", defer
        # to title/classifier rather than guess.
        (0, None),
        (1, "apm"),
        (2, "apm"),
        (3, "pm"),
        (5, "pm"),
        (6, "senior_pm"),
        (12, "senior_pm"),
    ],
)
def test_seniority_from_years_is_always_a_valid_enum_member(
    years: int, expected: str | None
) -> None:
    from job_assist.adapters.wellfound import _seniority_from_years
    from job_assist.db.enums import SeniorityLevel

    result = _seniority_from_years(years)
    assert result == expected
    # The real guard: a non-None return MUST be a Postgres enum value, or the
    # INSERT raises and the per-company ingest_run fails. (None = no hint, fine.)
    assert result is None or result in {m.value for m in SeniorityLevel}


def test_map_record_low_years_maps_to_valid_apm_not_associate_pm() -> None:
    # End-to-end through the mapper: a junior role (years_experience_min=1) must
    # land 'apm', never the off-enum 'associate_pm' that broke prod inserts.
    from job_assist.db.enums import SeniorityLevel

    np = map_wellfound_record(
        _rec(title="Revenue Operations Manager", years_experience_min=1),
        "FlexPoint",
        source_job_id="y",
    )
    assert np.seniority_level == "apm"
    assert np.seniority_level in {m.value for m in SeniorityLevel}


def test_company_name_of_reads_flat_company_name() -> None:
    assert company_name_of(_rec()) == "Transfix"
    assert company_name_of({"company_name": "Flat Co"}) == "Flat Co"
    assert company_name_of({"company": {"name": "Nested Co"}}) == "Nested Co"
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
    keep = _rec(id="1")  # default fixture: Scale Stage / Top Investors → kept
    # No legitimacy badge + no salary → fails the gate.
    drop = _rec(id="2", company_badges=["Actively Hiring"], compensation_parsed={})
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
