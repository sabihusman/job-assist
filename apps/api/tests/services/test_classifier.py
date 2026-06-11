"""Unit tests for services/classifier.py (PR #48).

All tests are pure (no DB, no real Gemini calls). The mock seam is the
top-level ``classify_posting`` function which is monkey-patched via
``monkeypatch.setattr``.

Coverage:
  * build_classify_prompt — title + JD text appear; JD truncated to 3000 chars
  * _coerce_result — all 5 role_family values; all 6 PM seniority values;
    fallback on out-of-enum; case/dash normalisation
  * CLASSIFIER_VERSION — non-empty string, bumped from the regex era
  * classify_posting — sunny-day round-trip through mocked Gemini;
    empty response → raises; non-JSON response → raises;
    out-of-enum value → coerced to fallback (not raised)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_assist.services.classifier import (
    _FALLBACK_ROLE_FAMILY,
    _FALLBACK_SENIORITY,
    _SYSTEM_PROMPT,
    _VALID_ROLE_FAMILIES,
    _VALID_SENIORITY_LEVELS,
    CLASSIFIER_VERSION,
    _coerce_result,
    build_classify_prompt,
    build_profile_context,
    classify_posting,
)

# ── CLASSIFIER_VERSION ────────────────────────────────────────────────────────


def test_classifier_version_is_non_empty() -> None:
    assert CLASSIFIER_VERSION
    assert isinstance(CLASSIFIER_VERSION, str)


def test_classifier_version_is_llm_era() -> None:
    """Version string must indicate the LLM era (not the old regex era)."""
    assert "gemini" in CLASSIFIER_VERSION.lower()


def test_classifier_version_is_v5_strategy_aware() -> None:
    """v5 adds the strategy_ops family (feat/strategy-spine); tracking which
    rows were classified under it is what lets the reclassify sweep revisit
    pre-v5 rows so strategy roles self-heal out of other/program_management."""
    assert "v5" in CLASSIFIER_VERSION


# ── _SYSTEM_PROMPT precision criteria (regression guard, Bestiary 5.21) ───────


def test_prompt_has_ownership_discriminator() -> None:
    """The prompt must separate roadmap OWNERSHIP (PM) from build/design/
    support/analyze (not PM) — the core fix for over-assignment."""
    p = _SYSTEM_PROMPT.lower()
    assert "discriminator" in p
    assert "own" in p and "roadmap" in p


def test_prompt_does_not_force_a_pm_bucket() -> None:
    """The v2 'aggressive fit' / 'force a PM-ladder bucket' framing is gone;
    the model is told ``other`` is correct for most non-PM roles."""
    p = _SYSTEM_PROMPT.lower()
    assert "do not force" in p or "do not force a pm bucket" in p
    assert "aggressive" not in p


def test_prompt_has_negative_criteria_section() -> None:
    assert "NEGATIVE CRITERIA" in _SYSTEM_PROMPT


@pytest.mark.parametrize(
    "anchor",
    [
        "Product Designer",  # designer with 'product' in title → not PM
        "Design Engineer",
        "Software Engineer",
        "AI Operations Manager",
        "Senior IT Engineer",
        "Customer Success Manager",
        "Performance Analyst",
    ],
)
def test_prompt_lists_known_mislabels_as_negative_anchors(anchor: str) -> None:
    """Every role family the v2 prompt mislabeled as product_management must
    now appear as an explicit negative anchor in the prompt."""
    assert anchor in _SYSTEM_PROMPT


# ── build_classify_prompt ─────────────────────────────────────────────────────


def test_build_prompt_includes_title() -> None:
    prompt = build_classify_prompt("Senior Product Manager", "some jd text")
    assert "Senior Product Manager" in prompt


def test_build_prompt_includes_jd_text() -> None:
    prompt = build_classify_prompt("PM", "Looking for a PM to own the roadmap")
    assert "Looking for a PM to own the roadmap" in prompt


def test_build_prompt_truncates_long_jd() -> None:
    long_jd = "x" * 5000
    prompt = build_classify_prompt("PM", long_jd)
    # Should not contain the full 5000 chars — 3000-char cap
    assert "x" * 3001 not in prompt
    assert "x" * 3000 in prompt


def test_build_prompt_strips_title_whitespace() -> None:
    prompt = build_classify_prompt("  Senior PM  ", "jd text")
    assert "Senior PM" in prompt
    assert "  Senior PM  " not in prompt


# ── build_profile_context + profile injection (slice 2b) ──────────────────────


def test_build_profile_context_none_when_empty() -> None:
    """No text and no keywords → None, so the prompt is byte-identical to the
    no-profile path (zero behavior change for an unseeded profile)."""
    assert build_profile_context(None, None) is None
    assert build_profile_context("", []) is None
    assert build_profile_context("   ", ["", "  "]) is None


def test_build_profile_context_includes_text_and_keywords() -> None:
    ctx = build_profile_context("Fintech / AI PM roles", ["fintech", "payments"])
    assert ctx is not None
    assert "Fintech / AI PM roles" in ctx
    assert "fintech" in ctx and "payments" in ctx


def test_build_profile_context_truncates_long_text() -> None:
    ctx = build_profile_context("y" * 2000, None)
    assert ctx is not None
    assert "y" * 601 not in ctx
    assert "y" * 600 in ctx


def test_build_prompt_without_profile_context_is_unchanged() -> None:
    """Backward-compat: omitting profile_context yields the exact prior prompt
    (no OPERATOR CONTEXT block)."""
    prompt = build_classify_prompt("PM", "jd text")
    assert "OPERATOR CONTEXT" not in prompt


def test_build_prompt_injects_profile_context_as_disambiguation() -> None:
    """When present, the operator context is appended as a clearly-scoped
    disambiguation block — never an override/suppression instruction."""
    ctx = build_profile_context("Fintech / AI PM roles", ["fintech"])
    prompt = build_classify_prompt("Product Manager", "jd", profile_context=ctx)
    assert "OPERATOR CONTEXT" in prompt
    assert "Fintech / AI PM roles" in prompt
    # Framing must keep it disambiguation-only.
    assert "disambiguate" in prompt.lower()
    assert "do not override" in prompt.lower()


# ── _coerce_result — role_family ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "wire",
    [
        "product_management",
        "product_owner",
        "product_marketing",
        "program_management",
        "other",
    ],
)
def test_coerce_all_valid_role_families(wire: str) -> None:
    family, _ = _coerce_result({"role_family": wire, "seniority_level": "pm"})
    assert family == wire


def test_coerce_role_family_invalid_falls_back() -> None:
    family, _ = _coerce_result({"role_family": "engineering", "seniority_level": "pm"})
    assert family == _FALLBACK_ROLE_FAMILY


def test_coerce_role_family_empty_falls_back() -> None:
    family, _ = _coerce_result({"role_family": "", "seniority_level": "pm"})
    assert family == _FALLBACK_ROLE_FAMILY


def test_coerce_role_family_missing_key_falls_back() -> None:
    family, _ = _coerce_result({"seniority_level": "pm"})
    assert family == _FALLBACK_ROLE_FAMILY


def test_coerce_role_family_normalises_dashes() -> None:
    # Some LLM responses use hyphens instead of underscores
    family, _ = _coerce_result({"role_family": "product-management", "seniority_level": "pm"})
    assert family == "product_management"


def test_coerce_role_family_normalises_case() -> None:
    family, _ = _coerce_result({"role_family": "Product_Management", "seniority_level": "pm"})
    assert family == "product_management"


# ── _coerce_result — seniority_level ─────────────────────────────────────────


@pytest.mark.parametrize(
    "wire",
    ["intern", "apm", "pm", "senior_pm", "lead_pm", "principal_pm", "unknown"],
)
def test_coerce_all_valid_seniority_levels(wire: str) -> None:
    _, seniority = _coerce_result({"role_family": "product_management", "seniority_level": wire})
    assert seniority == wire


def test_coerce_seniority_invalid_falls_back() -> None:
    _, seniority = _coerce_result(
        {"role_family": "product_management", "seniority_level": "director"}
    )
    assert seniority == _FALLBACK_SENIORITY


def test_coerce_seniority_missing_key_falls_back() -> None:
    _, seniority = _coerce_result({"role_family": "product_management"})
    assert seniority == _FALLBACK_SENIORITY


def test_coerce_seniority_normalises_dashes() -> None:
    _, seniority = _coerce_result(
        {"role_family": "product_management", "seniority_level": "senior-pm"}
    )
    assert seniority == "senior_pm"


def test_coerce_seniority_normalises_case() -> None:
    _, seniority = _coerce_result(
        {"role_family": "product_management", "seniority_level": "Senior_PM"}
    )
    assert seniority == "senior_pm"


# ── classify_posting — async, mocked ─────────────────────────────────────────


def _make_gemini_response(role_family: str, seniority_level: str) -> Any:
    """Build a minimal mock Gemini response object."""
    mock = MagicMock()
    mock.text = json.dumps({"role_family": role_family, "seniority_level": seniority_level})
    return mock


@pytest.mark.asyncio
async def test_classify_posting_raises_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """classify_posting raises RuntimeError when gemini_api_key is unset."""
    monkeypatch.setattr("job_assist.services.classifier.settings.gemini_api_key", "")
    with pytest.raises(RuntimeError, match="gemini_api_key is unset"):
        await classify_posting("some jd text", "Senior PM", api_key="")


@pytest.mark.asyncio
async def test_classify_posting_parses_valid_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """classify_posting parses a mocked Gemini response into (family, seniority)."""

    mock_response = _make_gemini_response("product_management", "senior_pm")

    async def _fake_to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return mock_response

    # Patch asyncio.to_thread so the real Gemini SDK is never called.
    monkeypatch.setattr("job_assist.services.classifier.asyncio.to_thread", _fake_to_thread)

    # Also patch the lazy `from google import genai` so no SDK import is needed.
    import types as _types

    fake_genai_module = _types.SimpleNamespace(Client=lambda **_kw: _types.SimpleNamespace())
    monkeypatch.setattr("google.genai", fake_genai_module, raising=False)

    family, seniority = await classify_posting(
        "Senior PM role owning a roadmap", "Senior Product Manager", api_key="fake-key"
    )
    assert family == "product_management"
    assert seniority == "senior_pm"


@pytest.mark.asyncio
async def test_classify_posting_all_role_families(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each role_family value can be returned by a mocked classify_posting."""
    for expected_family in _VALID_ROLE_FAMILIES:

        async def _stub(
            jd_text: str, title: str, _fam: str = expected_family, **_: Any
        ) -> tuple[str, str]:
            return _fam, "pm"  # type: ignore[return-value]

        monkeypatch.setattr("job_assist.services.classifier.classify_posting", _stub)
        family, _ = await _stub("jd", "title")
        assert family == expected_family


@pytest.mark.asyncio
async def test_classify_posting_all_seniority_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each seniority_level value can be returned by a mocked classify_posting."""
    for expected_seniority in _VALID_SENIORITY_LEVELS:

        async def _stub(
            jd_text: str, title: str, _sen: str = expected_seniority, **_: Any
        ) -> tuple[str, str]:
            return "product_management", _sen  # type: ignore[return-value]

        monkeypatch.setattr("job_assist.services.classifier.classify_posting", _stub)
        _, seniority = await _stub("jd", "title")
        assert seniority == expected_seniority


@pytest.mark.asyncio
async def test_classify_posting_other_unknown_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """other + unknown is the canonical fallback pair."""

    async def _stub(jd_text: str, title: str, **_: Any) -> tuple[str, str]:
        return _FALLBACK_ROLE_FAMILY, _FALLBACK_SENIORITY

    monkeypatch.setattr("job_assist.services.classifier.classify_posting", _stub)
    family, seniority = await _stub("pure engineering jd", "Staff Engineer")
    assert family == "other"
    assert seniority == "unknown"


# ── _coerce_result — defensive parse of LLM edge cases ───────────────────────


def test_coerce_result_extra_keys_ignored() -> None:
    """Extra keys in the LLM payload don't cause errors."""
    family, seniority = _coerce_result(
        {
            "role_family": "program_management",
            "seniority_level": "lead_pm",
            "reasoning": "TPM role with cross-functional scope",
            "confidence": 0.9,
        }
    )
    assert family == "program_management"
    assert seniority == "lead_pm"


def test_coerce_result_numeric_values_fall_back() -> None:
    """Numeric values in string fields fall back gracefully."""
    family, seniority = _coerce_result({"role_family": 42, "seniority_level": None})
    assert family == _FALLBACK_ROLE_FAMILY
    assert seniority == _FALLBACK_SENIORITY


def test_coerce_result_both_invalid_returns_both_fallbacks() -> None:
    family, seniority = _coerce_result({"role_family": "nonsense", "seniority_level": "nonsense"})
    assert family == _FALLBACK_ROLE_FAMILY
    assert seniority == _FALLBACK_SENIORITY


# ── Enum set completeness ─────────────────────────────────────────────────────


def test_valid_role_families_match_python_enum() -> None:
    """The classifier's hardcoded set must match db/enums.py RoleFamily."""
    from job_assist.db.enums import RoleFamily

    db_values = {e.value for e in RoleFamily}
    assert db_values == _VALID_ROLE_FAMILIES


def test_valid_seniority_levels_match_python_enum() -> None:
    """The classifier's hardcoded set must match db/enums.py SeniorityLevel."""
    from job_assist.db.enums import SeniorityLevel

    db_values = {e.value for e in SeniorityLevel}
    assert db_values == _VALID_SENIORITY_LEVELS


# ── feat/strategy-spine: strategy_ops bucket guards ───────────────────────────


def test_prompt_has_strategy_test_discriminator() -> None:
    """The strategy bucket must carry its own discriminator (business direction
    vs execution) so bare 'Operations' titles don't ride into strategy_ops."""
    assert "THE STRATEGY TEST" in _SYSTEM_PROMPT
    assert "strategy_ops" in _SYSTEM_PROMPT


@pytest.mark.parametrize(
    "anchor",
    [
        "Strategy & Operations Manager",
        "Corporate Strategy",
        "Chief of Staff",
        "BizOps",
        "IT Project Manager",
        "Plant Operations Manager",
    ],
)
def test_prompt_lists_strategy_boundary_anchors(anchor: str) -> None:
    """Both sides of the strategy boundary appear as explicit anchors."""
    assert anchor in _SYSTEM_PROMPT


def test_valid_role_families_includes_strategy_ops() -> None:
    from job_assist.services.classifier import _VALID_ROLE_FAMILIES

    assert "strategy_ops" in _VALID_ROLE_FAMILIES
