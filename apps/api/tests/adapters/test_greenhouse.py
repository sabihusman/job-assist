"""Tests for the Greenhouse adapter and IngestionService.

Unit tests (no DB, no network):
  test_normalize_seniority_role_family  — seniority + role_family heuristics
  test_normalize_location               — location parsing / remote_type
  test_normalize_html_stripping         — selectolax strips tags, keeps text

Integration tests (require TEST_DATABASE_URL):
  test_idempotency                — ingest twice → same row count
  test_partial_update_jd_hash     — jd_text change → hash refreshes, no dup
  test_fk_linking                 — target_company.ats_handle seeded → FK set
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select

from job_assist.adapters.base import RawPosting
from job_assist.adapters.greenhouse import (
    GreenhouseAdapter,
    detect_role_family,
    detect_seniority,
    normalize_title,
    parse_location,
    strip_html,
)

_FIXTURE_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "greenhouse_stripe.json"
_FIXTURE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_adapter(jobs: list[dict[str, Any]] | None = None) -> GreenhouseAdapter:
    """Return a GreenhouseAdapter backed by a mock client returning *jobs*."""
    payload = {"jobs": jobs if jobs is not None else _FIXTURE["jobs"]}
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return GreenhouseAdapter(client=mock_client)


# ── Unit tests ─────────────────────────────────────────────────────────────────


class TestNormalizeSeniorityRoleFamily:
    """Verify seniority and role_family derivation from title strings."""

    @pytest.mark.parametrize(
        "raw_title, expected_seniority, expected_role_family",
        [
            # Senior PM variations
            ("Senior Product Manager, Payments", "senior_pm", "product_management"),
            ("Sr. PM, Risk", "senior_pm", "product_management"),
            # Associate / APM
            ("APM, Infrastructure", "apm", "product_management"),
            ("Associate Product Manager", "apm", "product_management"),
            # Lead / Staff
            ("Lead Product Manager", "lead_pm", "product_management"),
            ("Staff PM, Growth", "lead_pm", "product_management"),
            # Principal
            ("Principal Product Manager", "principal_pm", "product_management"),
            # Group / Head / Director / VP — leveled up so the seniority
            # hard-filter catches them (feat/seniority-parser-precision).
            ("Group Product Manager, Credit", "lead_pm", "product_management"),
            ("Group PM, Payments", "lead_pm", "product_management"),
            # "head of product" → no "product manager/management" substring,
            # so role_family is ``other``; seniority still levels to principal.
            ("Head of Product", "principal_pm", "other"),
            ("Director of Product Management", "principal_pm", "product_management"),
            ("VP, Product Management", "principal_pm", "product_management"),
            ("Vice President, Product Management", "principal_pm", "product_management"),
            # Guard: "group" as a TEAM noun (not a level) stays pm.
            ("Product Manager, Payments Group", "pm", "product_management"),
            # Guard: "lead generation" is a marketing function, NOT a
            # seniority level — must not level up to lead_pm.
            ("Lead Generation Manager", "unknown", "other"),
            # Intern
            ("Product Management Intern", "intern", "product_management"),
            # Product Owner
            ("Senior Product Owner", "senior_pm", "product_owner"),
            # Product Marketing
            ("Product Marketing Manager", "unknown", "product_marketing"),
            # Program Manager
            ("Senior Program Manager", "senior_pm", "program_management"),
            # Unknown / catch-all
            ("Engineering Manager", "unknown", "other"),
            ("PM, Risk", "pm", "product_management"),
        ],
    )
    def test_heuristics(
        self,
        raw_title: str,
        expected_seniority: str,
        expected_role_family: str,
    ) -> None:
        norm = normalize_title(raw_title)
        assert detect_seniority(norm) == expected_seniority, f"seniority mismatch for {raw_title!r}"
        assert detect_role_family(norm) == expected_role_family, (
            f"role_family mismatch for {raw_title!r}"
        )


class TestNormalizeLocation:
    """Verify location parsing and remote_type derivation."""

    @pytest.mark.parametrize(
        "location_raw, expected_remote_type, first_city",
        [
            ("Remote", "remote", None),
            ("San Francisco, CA", "onsite", "San Francisco"),
            ("New York, NY", "onsite", "New York"),
            ("London", "unknown", "London"),
            ("Remote / New York, NY", "remote", None),
            (None, "unknown", None),
        ],
    )
    def test_parse(
        self,
        location_raw: str | None,
        expected_remote_type: str,
        first_city: str | None,
    ) -> None:
        locs, rt = parse_location(location_raw)
        assert rt == expected_remote_type
        if first_city is not None:
            assert locs[0].get("city") == first_city


class TestNormalizeHtmlStripping:
    """Verify HTML stripping preserves text content without tags."""

    def test_strips_tags(self) -> None:
        html = "<p>Hello <strong>world</strong></p><ul><li>Item 1</li><li>Item 2</li></ul>"
        result = strip_html(html)
        assert "<" not in result
        assert "Hello" in result
        assert "world" in result
        assert "Item 1" in result
        assert "Item 2" in result

    def test_empty_input(self) -> None:
        assert strip_html("") == ""

    def test_fixture_content_stripped(self) -> None:
        html = _FIXTURE["jobs"][0]["content"]
        result = strip_html(html)
        assert "<" not in result
        assert "Senior Product Manager" in result
        assert "Lead the product roadmap" in result


def _gh_job(content: str, *, job_id: int = 999, title: str = "Product Manager") -> dict[str, Any]:
    """Minimal Greenhouse job payload for normalize() tests."""
    return {
        "id": job_id,
        "title": title,
        "location": {"name": "Remote"},
        "absolute_url": "https://example.test/jobs/999",
        "content": content,
        "first_published": "2026-05-01T00:00:00Z",
        "departments": [],
    }


class TestEntityEscapedHtml:
    """Bestiary 5.17: Greenhouse content is entity-escaped HTML.

    normalize() must html.unescape before strip_html so escaped tags
    don't survive as literal visible text in jd_text.
    """

    def test_escaped_html_produces_clean_jd_text(self) -> None:
        # Greenhouse delivers content like this — entities, not real tags.
        escaped = (
            "&lt;h2&gt;&lt;strong&gt;About Us&lt;/strong&gt;&lt;/h2&gt;"
            "&lt;p&gt;We build &lt;a href=&quot;x&quot;&gt;things&lt;/a&gt;.&lt;/p&gt;"
        )
        norm = GreenhouseAdapter().normalize(
            RawPosting(source_job_id="999", raw_payload=_gh_job(escaped)),
            "ExampleCo",
        )
        # No literal angle brackets and no surviving entities.
        assert "<" not in norm.jd_text
        assert ">" not in norm.jd_text
        assert "&lt;" not in norm.jd_text
        assert "&gt;" not in norm.jd_text
        # The actual prose survives.
        assert "About Us" in norm.jd_text
        assert "We build" in norm.jd_text
        assert "things" in norm.jd_text


class TestGreenhouseSalaryExtraction:
    """JD-body salary text-mining (public Greenhouse API has no pay field)."""

    def test_jd_body_pay_range_populates_salary(self) -> None:
        # Pay-transparency board pattern: range embedded in (escaped) HTML.
        escaped = (
            "&lt;p&gt;The base pay range for this role is "
            "$180,000&lt;span&gt;&amp;mdash;&lt;/span&gt;$248,000 USD.&lt;/p&gt;"
        )
        norm = GreenhouseAdapter().normalize(
            RawPosting(source_job_id="999", raw_payload=_gh_job(escaped)),
            "ExampleCo",
        )
        assert norm.salary_min == 180000
        assert norm.salary_max == 248000
        assert norm.salary_currency == "USD"

    def test_no_pay_in_body_leaves_salary_none(self) -> None:
        escaped = "&lt;p&gt;We are hiring a Product Manager. No comp listed.&lt;/p&gt;"
        norm = GreenhouseAdapter().normalize(
            RawPosting(source_job_id="999", raw_payload=_gh_job(escaped)),
            "ExampleCo",
        )
        assert norm.salary_min is None
        assert norm.salary_max is None
        assert norm.salary_currency is None


# ── HandleNotFoundError (Bestiary 5.9) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_404_raises_handle_not_found() -> None:
    """A 404 from the listing endpoint surfaces as HandleNotFoundError
    so the orchestrator can record a distinct ``handle_not_found``
    status instead of conflating with the generic "empty" success."""
    from job_assist.adapters.base import HandleNotFoundError

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    adapter = GreenhouseAdapter(client=mock_client)

    with pytest.raises(HandleNotFoundError) as exc_info:
        await adapter.fetch_postings("nonexistent")
    assert exc_info.value.ats == "greenhouse"
    assert exc_info.value.handle == "nonexistent"
    assert "greenhouse.io" in exc_info.value.url


@pytest.mark.asyncio
async def test_non_200_non_404_still_returns_empty() -> None:
    """Only 404 raises. Other 4xx (e.g. 418) keep the historical
    silent-return path so unrelated upstream blips don't fail the run."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 418
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)
    adapter = GreenhouseAdapter(client=mock_client)
    assert await adapter.fetch_postings("teapot") == []


@pytest.mark.asyncio
async def test_timeout_propagates_not_swallowed() -> None:
    """Bestiary 5.19: a retry-exhausted timeout PROPAGATES — it must NOT be
    swallowed as ``[]``. A populated board silently turning empty would let
    stale-detection close every posting on it. ``_get`` already retries +
    reraises; this asserts ``fetch_postings`` doesn't bury the result."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    adapter = GreenhouseAdapter(client=mock_client)
    # Replace the (retrying) _get with a direct raise — simulates retry
    # exhaustion without the tenacity backoff delay.
    adapter._get = AsyncMock(side_effect=httpx.ReadTimeout("slow board"))  # type: ignore[method-assign]
    with pytest.raises(httpx.TimeoutException):
        await adapter.fetch_postings("anthropic")


# ── Integration tests ──────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_idempotency(db_session: Any) -> None:
    """Ingesting the same fixture twice must not create duplicate rows."""
    from sqlalchemy import func, select

    from job_assist.db.models.job_posting import JobPosting
    from job_assist.services.ingestion import IngestionService

    adapter = _make_adapter()
    service = IngestionService()

    await service.ingest_source(adapter, "stripe", db_session)
    count_1: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()

    # Second ingest with identical data
    adapter2 = _make_adapter()
    await service.ingest_source(adapter2, "stripe", db_session)
    count_2: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()

    assert count_1 == len(_FIXTURE["jobs"]), "First ingest should insert all fixture rows"
    assert count_1 == count_2, "Second ingest with same data must not add rows"


@_NEEDS_DB
async def test_partial_update_jd_hash(db_session: Any) -> None:
    """When jd_text changes, the hash is updated and no duplicate row is created."""
    from job_assist.db.models.job_posting import JobPosting
    from job_assist.services.ingestion import IngestionService

    adapter = _make_adapter()
    service = IngestionService()
    await service.ingest_source(adapter, "stripe", db_session)

    # Mutate the HTML content of the first job
    mutated_jobs: list[dict[str, Any]] = json.loads(json.dumps(_FIXTURE["jobs"]))
    mutated_jobs[0]["content"] = "<p>Updated job description — new content here.</p>"
    adapter2 = _make_adapter(jobs=mutated_jobs)
    await service.ingest_source(adapter2, "stripe", db_session)

    # Still the same number of JobPostings (no duplicate)
    total: int = (
        await db_session.execute(select(func.count()).select_from(JobPosting))
    ).scalar_one()
    assert total == len(_FIXTURE["jobs"])

    # The mutated job's jd_text_hash must have changed
    original_hash = (
        GreenhouseAdapter()
        .normalize(
            RawPosting(
                source_job_id=str(_FIXTURE["jobs"][0]["id"]),
                raw_payload=_FIXTURE["jobs"][0],
            ),
            "Stripe",
        )
        .jd_text_hash
    )

    row = (
        await db_session.execute(
            select(JobPosting).where(
                JobPosting.content_hash
                == GreenhouseAdapter()
                .normalize(
                    RawPosting(
                        source_job_id=str(mutated_jobs[0]["id"]),
                        raw_payload=mutated_jobs[0],
                    ),
                    "Stripe",
                )
                .content_hash
            )
        )
    ).scalar_one()

    assert row.jd_text_hash != original_hash, "jd_text_hash should reflect the updated content"
    assert "Updated job description" in row.jd_text


@_NEEDS_DB
async def test_fk_linking(db_session: Any) -> None:
    """When a target_company with ats_handle='stripe' is seeded, FK must be set."""
    from job_assist.db.models.job_posting import JobPosting
    from job_assist.db.models.target_company import TargetCompany
    from job_assist.services.ingestion import IngestionService

    # Seed a target_company for stripe
    company = TargetCompany(
        name="Stripe",
        ats="greenhouse",  # type: ignore[arg-type]
        ats_handle="stripe",
        tier=1,
    )
    db_session.add(company)
    await db_session.flush()

    adapter = _make_adapter()
    service = IngestionService()
    await service.ingest_source(adapter, "stripe", db_session)

    postings = (await db_session.execute(select(JobPosting))).scalars().all()
    assert len(postings) == len(_FIXTURE["jobs"])
    for posting in postings:
        assert posting.target_company_id == company.id, (
            f"FK not set on posting {posting.normalized_title!r}"
        )
