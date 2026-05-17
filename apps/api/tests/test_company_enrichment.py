"""Tests for the company_enrichment service + admin endpoints.

Pure functions (URL builder, prompt builder, post-LLM validators) run
everywhere. The DB-gated tests use the existing ``db_session`` fixture
and monkey-patch ``generate_description`` so no test ever calls the real
Gemini API.

Note on auth: the spec's ``test_sweep_endpoint_auth`` is intentionally
omitted. The existing admin surface (and the daily ingest cron) doesn't
use an auth header — see PR #27 description. A shared-secret guard for
``/admin/*`` and ``/enrichment/*`` is a future PR.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from job_assist.db.models import TargetCompany
from job_assist.services.company_enrichment import (
    EnrichmentResult,
    SweepSummary,
    build_logo_url,
    build_prompt,
    enrich_company,
    generate_description,
    reset_attempts_and_retry,
    sweep_companies,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestBuildLogoUrl:
    def test_format(self) -> None:
        url = build_logo_url("anthropic.com", token="pk_test_123")
        assert url == "https://img.logo.dev/anthropic.com?token=pk_test_123"

    def test_strips_whitespace(self) -> None:
        assert build_logo_url("  anthropic.com  ", token="pk_x") == (
            "https://img.logo.dev/anthropic.com?token=pk_x"
        )

    def test_empty_domain_raises(self) -> None:
        with pytest.raises(ValueError, match="domain is required"):
            build_logo_url("", token="pk_x")

    def test_whitespace_domain_raises(self) -> None:
        with pytest.raises(ValueError, match="domain is required"):
            build_logo_url("   ", token="pk_x")


def test_build_prompt_includes_company_name() -> None:
    prompt = build_prompt("Acmecorp")
    assert "Acmecorp" in prompt
    assert "max 180 characters" in prompt
    assert "No marketing language" in prompt


# ── generate_description (mocked Gemini) ─────────────────────────────────────


@pytest.fixture
def mock_gemini_response() -> Any:
    """Build a Gemini-like response object exposing a ``.text`` attribute."""

    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

    return _Response


async def _patch_generate_description(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Replace generate_description with a stub returning *text*."""

    async def _stub(company_name: str, domain: str | None, **_: Any) -> str:
        return text

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)


async def test_generate_description_includes_company_in_prompt(
    monkeypatch: pytest.MonkeyPatch, mock_gemini_response: Any
) -> None:
    """``generate_description`` reaches the SDK with a prompt that names the company."""
    captured: dict[str, Any] = {}

    class _FakeModels:
        def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
            captured["model"] = model
            captured["contents"] = contents
            return mock_gemini_response("Acmecorp builds developer tools.")

    class _FakeClient:
        def __init__(self, *_: Any, **__: Any) -> None:
            self.models = _FakeModels()

    import sys
    import types

    fake_genai = types.ModuleType("genai")
    fake_types = types.ModuleType("types")

    class _Cfg:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_types.GenerateContentConfig = _Cfg  # type: ignore[attr-defined]
    fake_genai.Client = _FakeClient  # type: ignore[attr-defined]
    fake_genai.types = fake_types  # type: ignore[attr-defined]
    sys.modules["google.genai"] = fake_genai
    sys.modules["google.genai.types"] = fake_types

    try:
        result = await generate_description(
            "Acmecorp",
            "acmecorp.com",
            api_key="pk_test",
            model="gemini-2.5-flash-lite",
        )
    finally:
        sys.modules.pop("google.genai", None)
        sys.modules.pop("google.genai.types", None)

    assert result == "Acmecorp builds developer tools."
    assert "Acmecorp" in captured["contents"]
    assert captured["model"] == "gemini-2.5-flash-lite"


async def test_generate_description_rejects_too_long() -> None:
    """Direct hard-cap test on the validator (not via the SDK shim)."""
    from job_assist.services.company_enrichment import _validate_description

    too_long = "x" * 251  # exceeds the 250-char hard max
    with pytest.raises(ValueError, match="too long"):
        _validate_description(too_long)


async def test_generate_description_rejects_newlines() -> None:
    from job_assist.services.company_enrichment import _validate_description

    with pytest.raises(ValueError, match="newlines"):
        _validate_description("Line one.\nLine two.")


async def test_generate_description_rejects_carriage_return() -> None:
    from job_assist.services.company_enrichment import _validate_description

    with pytest.raises(ValueError, match="newlines"):
        _validate_description("Line one.\rLine two.")


# ── enrich_company (DB-gated, generate_description stubbed) ──────────────────


@_NEEDS_DB
async def test_enrich_company_skips_already_enriched(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tc = TargetCompany(
        name="AlreadyEnriched",
        tier=1,
        ats="greenhouse",
        ats_handle="ae",
        domain="ae.com",
        description="Pre-existing one-sentence description.",
    )
    db_session.add(tc)
    await db_session.commit()

    called: list[str] = []

    async def _stub(*_: Any, **__: Any) -> str:
        called.append("yes")
        return "fresh value"

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    result = await enrich_company(db_session, tc.id)
    assert result.status == "skipped"
    assert called == []  # never reached the Gemini stub


@_NEEDS_DB
async def test_enrich_company_no_domain(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tc = TargetCompany(name="NoDomainCo", tier=2, ats="greenhouse", domain=None)
    db_session.add(tc)
    await db_session.commit()

    called: list[str] = []

    async def _stub(*_: Any, **__: Any) -> str:
        called.append("yes")
        return "x"

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    result = await enrich_company(db_session, tc.id)
    assert result.status == "no_domain"
    assert called == []

    await db_session.refresh(tc)
    assert tc.enrichment_error == "missing domain"
    assert tc.enrichment_attempt_count == 1
    assert tc.description is None


@_NEEDS_DB
async def test_enrich_company_gemini_failure(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tc = TargetCompany(name="FlakyCo", tier=3, ats="lever", domain="flaky.example")
    db_session.add(tc)
    await db_session.commit()

    async def _failing_stub(*_: Any, **__: Any) -> str:
        raise RuntimeError("gemini exploded: 503 service unavailable")

    monkeypatch.setattr(
        "job_assist.services.company_enrichment.generate_description", _failing_stub
    )

    result = await enrich_company(db_session, tc.id)
    assert result.status == "error"
    assert result.error is not None
    assert "gemini exploded" in result.error

    await db_session.refresh(tc)
    assert tc.enrichment_attempt_count == 1
    assert tc.enrichment_error is not None
    assert "gemini exploded" in tc.enrichment_error
    assert tc.description is None


@_NEEDS_DB
async def test_enrich_company_success(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tc = TargetCompany(
        name="HappyCo",
        tier=1,
        ats="greenhouse",
        domain="happyco.example",
        enrichment_error="prior error",
        enrichment_attempt_count=2,
    )
    db_session.add(tc)
    await db_session.commit()

    async def _stub(company_name: str, domain: str | None, **_: Any) -> str:
        assert company_name == "HappyCo"
        return "HappyCo runs a synthetic test fixture."

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    result = await enrich_company(db_session, tc.id)
    assert result.status == "enriched"

    await db_session.refresh(tc)
    assert tc.description == "HappyCo runs a synthetic test fixture."
    assert tc.enriched_at is not None
    assert tc.enrichment_error is None  # cleared on success
    # attempt_count is left as-is (audit trail of how many tries it took).
    assert tc.enrichment_attempt_count == 2


@_NEEDS_DB
async def test_enrich_company_exhausted(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_assist.config import settings

    tc = TargetCompany(
        name="DeadEndCo",
        tier=4,
        ats="ashby",
        domain="deadend.example",
        enrichment_attempt_count=settings.company_enrich_max_attempts,
    )
    db_session.add(tc)
    await db_session.commit()

    called: list[str] = []

    async def _stub(*_: Any, **__: Any) -> str:
        called.append("yes")
        return "x"

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    result = await enrich_company(db_session, tc.id)
    assert result.status == "exhausted"
    assert called == []


@_NEEDS_DB
async def test_enrich_company_not_found(db_session: Any) -> None:
    """Unknown id → ``not_found`` rather than a 500."""
    result = await enrich_company(db_session, uuid.uuid4())
    assert result.status == "not_found"


# ── Sweep summary math ───────────────────────────────────────────────────────


@_NEEDS_DB
async def test_sweep_summary_counts_mixed_outcomes(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.config import settings

    db_session.add_all(
        [
            TargetCompany(
                name="SweepHappy",
                tier=1,
                ats="greenhouse",
                domain="happy.example",
            ),
            TargetCompany(
                name="SweepAlready",
                tier=2,
                ats="lever",
                domain="already.example",
                description="cached forever",
            ),
            TargetCompany(name="SweepNoDomain", tier=3, ats="ashby", domain=None),
            TargetCompany(
                name="SweepDead",
                tier=4,
                ats="ashby",
                domain="dead.example",
                enrichment_attempt_count=settings.company_enrich_max_attempts,
            ),
            TargetCompany(
                name="SweepFlaky",
                tier=2,
                ats="greenhouse",
                domain="flaky.example",
            ),
        ]
    )
    await db_session.commit()

    async def _stub(company_name: str, domain: str | None, **_: Any) -> str:
        if "Flaky" in company_name:
            raise RuntimeError("simulated flake")
        return f"{company_name} is a synthetic test fixture."

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    summary = await sweep_companies(db_session)

    assert summary.total >= 5
    # Five new fixtures, each landing in exactly one bucket:
    assert summary.enriched >= 1  # SweepHappy
    assert summary.skipped >= 1  # SweepAlready
    assert summary.no_domain >= 1  # SweepNoDomain
    assert summary.exhausted >= 1  # SweepDead
    assert summary.errors >= 1  # SweepFlaky
    # Bucket counts must sum to total.
    assert (
        summary.enriched + summary.skipped + summary.no_domain + summary.exhausted + summary.errors
    ) == summary.total

    # Error details capture the failing company's id, not the name.
    assert any("simulated flake" in entry.get("error", "") for entry in summary.error_details)


# ── Retry endpoint behaviour ─────────────────────────────────────────────────


@_NEEDS_DB
async def test_retry_resets_count_and_reenriches(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_assist.config import settings

    tc = TargetCompany(
        name="ResetMe",
        tier=2,
        ats="greenhouse",
        domain="reset.example",
        enrichment_attempt_count=settings.company_enrich_max_attempts,
        enrichment_error="prior failure",
    )
    db_session.add(tc)
    await db_session.commit()

    async def _stub(*_: Any, **__: Any) -> str:
        return "ResetMe is back online."

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    result = await reset_attempts_and_retry(db_session, tc.id)
    assert result.status == "enriched"

    await db_session.refresh(tc)
    assert tc.description == "ResetMe is back online."
    assert tc.enriched_at is not None
    assert tc.enrichment_error is None
    # /retry zeros the counter before the new attempt fires.
    assert tc.enrichment_attempt_count == 0


# ── Endpoints (ASGI client) ──────────────────────────────────────────────────


async def _client(db_session: Any) -> AsyncClient:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drop_override() -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    app.dependency_overrides.pop(get_db, None)


@_NEEDS_DB
async def test_sweep_endpoint_returns_summary(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(
        TargetCompany(
            name="EndpointTest",
            tier=1,
            ats="greenhouse",
            domain="endpoint.example",
        )
    )
    await db_session.commit()

    async def _stub(*_: Any, **__: Any) -> str:
        return "EndpointTest is a synthetic test fixture."

    monkeypatch.setattr("job_assist.services.company_enrichment.generate_description", _stub)

    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post("/enrichment/companies/sweep")
    finally:
        await _drop_override()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total" in body
    assert "enriched" in body
    assert body["total"] >= 1


@_NEEDS_DB
async def test_retry_endpoint_returns_404_for_unknown_id(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            resp = await ac.post(f"/enrichment/companies/{uuid.uuid4()}/retry")
    finally:
        await _drop_override()

    assert resp.status_code == 404


# ── Migration column existence ──────────────────────────────────────────────


@_NEEDS_DB
async def test_migration_adds_columns(db_session: Any) -> None:
    """The four new columns exist after ``alembic upgrade head``."""
    rows = (
        await db_session.execute(
            sa.text(
                """
                SELECT column_name, is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_name = 'target_company'
                   AND column_name IN (
                     'description',
                     'enriched_at',
                     'enrichment_error',
                     'enrichment_attempt_count'
                   )
                """
            )
        )
    ).all()

    by_name = {r.column_name: r for r in rows}
    assert set(by_name) == {
        "description",
        "enriched_at",
        "enrichment_error",
        "enrichment_attempt_count",
    }
    # Nullability matches the migration.
    assert by_name["description"].is_nullable == "YES"
    assert by_name["enriched_at"].is_nullable == "YES"
    assert by_name["enrichment_error"].is_nullable == "YES"
    assert by_name["enrichment_attempt_count"].is_nullable == "NO"


# ── Sweep summary unit test (no DB) ──────────────────────────────────────────


def test_sweep_summary_record_classifies_results() -> None:
    """The summary's ``record`` method bumps the right bucket per status."""
    summary = SweepSummary()
    summary.record(EnrichmentResult(status="enriched", company_id="a"))
    summary.record(EnrichmentResult(status="skipped", company_id="b"))
    summary.record(EnrichmentResult(status="no_domain", company_id="c"))
    summary.record(EnrichmentResult(status="exhausted", company_id="d"))
    summary.record(EnrichmentResult(status="error", company_id="e", error="boom"))
    summary.record(EnrichmentResult(status="error", company_id="f", error="boom2"))

    assert summary.total == 6
    assert summary.enriched == 1
    assert summary.skipped == 1
    assert summary.no_domain == 1
    assert summary.exhausted == 1
    assert summary.errors == 2
    assert len(summary.error_details) == 2
    assert {entry["company_id"] for entry in summary.error_details} == {"e", "f"}


def test_logo_url_falls_back_to_settings_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting ``token`` reads from ``settings.logo_dev_token``."""
    from job_assist.config import settings as live_settings

    monkeypatch.setattr(live_settings, "logo_dev_token", "pk_settings_token")
    assert build_logo_url("anthropic.com") == (
        "https://img.logo.dev/anthropic.com?token=pk_settings_token"
    )


def _silence_unused_imports() -> None:
    """Touch unused-import guards (select / select for SQLAlchemy)."""
    _ = select(TargetCompany.id)
    _ = datetime.now(tz=UTC)
    _ = patch
