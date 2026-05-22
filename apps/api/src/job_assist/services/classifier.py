"""LLM-based posting classifier (PR #48).

Reclassifies ``job_posting`` rows via Gemini to produce accurate
``role_family`` and ``seniority_level`` values.  Replaces the
ingest-time regex heuristics (``adapters/normalization.py``) for the
manual backfill sweep — the regex path remains active at ingest time
so every row gets an immediate classification without an API call.

Version history
───────────────
  v1 (regex)      — regex heuristic in normalization.py; never wrote a
                    ``classifier_version``; rows have NULL classifier_version.
  v2 (this file)  — Gemini Flash Lite with few-shot examples; written to
                    ``classifier_version`` on every sweep row.

Prompt design
─────────────
The prompt is aggressive about fitting into the 5 ``role_family`` buckets:
  * Enumerate ALL 5 values explicitly so the model never invents a 6th.
  * Few-shot examples cover the edge cases that fall through the regex:
    PMM, TPM, growth PM, ops-with-PM-scope, AI/ML-PM-adjacent.
  * ``other`` is reserved for genuine non-PM roles (pure engineering,
    operations, sales) where zero PM vocabulary appears.

For seniority the same aggression applies: force a PM-ladder bucket;
  * ``unknown`` only when the JD has NO level signal at all.

Mock seam
─────────
``classify_posting`` is a top-level module-level async function.  Tests
monkey-patch it via::

    monkeypatch.setattr(
        "job_assist.services.classifier.classify_posting",
        async_stub,
    )

No real Gemini calls anywhere in the test suite.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from job_assist.config import settings

logger = logging.getLogger(__name__)

# ── Version / model constants ─────────────────────────────────────────────────

CLASSIFIER_VERSION = "gemini-flash-lite-v2"
_MODEL_NAME = "gemini-2.5-flash-lite"

# Valid enum values — kept in sync with db/enums.py. The defensive parser
# below rejects any value not in these sets and falls back to the safe default.
_VALID_ROLE_FAMILIES = frozenset(
    {"product_management", "product_owner", "product_marketing", "program_management", "other"}
)
_VALID_SENIORITY_LEVELS = frozenset(
    {"intern", "apm", "pm", "senior_pm", "lead_pm", "principal_pm", "unknown"}
)

_FALLBACK_ROLE_FAMILY = "other"
_FALLBACK_SENIORITY = "unknown"

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are classifying job postings for a PM job-search tool. Your job is to
assign EXACTLY ONE value for each of two dimensions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIMENSION 1 — role_family
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Choose one of these FIVE values. Do not invent a sixth.

  product_management  — owns a product roadmap and works directly with
                        engineering. Includes "Growth PM", "AI PM",
                        "Platform PM", "ML Product Manager", and any
                        title containing "Product Manager" or "Head of
                        Product".
  product_owner       — Agile PO role, backlog management, sprint-level
                        scope. Usually titled "Product Owner".
  product_marketing   — go-to-market, messaging, positioning, launches.
                        Includes "PMM", "Product Marketing Manager",
                        "Senior PMM".
  program_management  — cross-functional coordination without a product
                        roadmap. Includes "TPM", "Technical Program
                        Manager", "Program Manager", "Operations PM"
                        where the JD is about process + coordination
                        rather than product vision.
  other               — use ONLY when the role has zero product scope:
                        pure software engineering, pure data science,
                        pure sales, recruiting, etc.

FEW-SHOT EXAMPLES (role_family):
  "Senior PMM, Growth" + JD about messaging → product_marketing
  "TPM, Infrastructure" + JD about cross-team coordination → program_management
  "Growth PM" + JD about A/B tests and funnel metrics → product_management
  "AI Product Strategist" + JD about ML platform roadmap → product_management
  "Director of Product Operations" + JD about process improvement, no roadmap → program_management
  "Product Owner" + JD about sprint backlog → product_owner
  "Staff Software Engineer" + JD about coding → other

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIMENSION 2 — seniority_level
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Choose one of these SEVEN values. This is a PM career ladder:

  intern        — internship, co-op, student program
  apm           — associate PM, rotational PM, new-grad PM
  pm            — mid-level PM; titles without a seniority qualifier
                  (e.g. "Product Manager") map here unless the JD text
                  implies a different level.
  senior_pm     — "Senior PM", "Senior Product Manager", "Sr PM"
  lead_pm       — "Lead PM", "Lead Product Manager", "Staff PM",
                  "Group PM", "Principal PM 1" (when the company has
                  a dual-track ladder where Principal = Staff)
  principal_pm  — "Principal PM", "Distinguished PM", "Fellow PM"
                  at the very top of the IC track
  unknown       — use ONLY when the JD contains NO level signal
                  whatsoever (no years-of-experience, no title qualifier,
                  no description of scope that implies a level)

FEW-SHOT EXAMPLES (seniority_level):
  "Senior PM" → senior_pm
  "Product Manager" (no qualifier, 3-5 yrs exp) → pm
  "Associate Product Manager" → apm
  "Lead Product Manager" → lead_pm
  "Principal PM" → principal_pm
  "PM Intern" → intern
  "Product Manager" with JD saying "10+ years" → senior_pm or lead_pm
    (pick based on scope described)
  Role with no level signal → unknown

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — respond ONLY with this JSON, no prose:
{"role_family": "<value>", "seniority_level": "<value>"}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ── Prompt builder ────────────────────────────────────────────────────────────


def build_classify_prompt(title: str, jd_text: str) -> str:
    """Build the user-turn message for a classify call.

    Kept as a pure function so unit tests can assert the title and
    JD-text appear in the prompt without needing a Gemini call.
    """
    # Truncate JD to keep token cost bounded.  The first 3000 chars
    # carry essentially all the signal needed for family + seniority.
    jd_snippet = (jd_text or "").strip()[:3000]
    return f"Title: {title.strip()}\n\nJob description:\n{jd_snippet}"


# ── Defensive coerce ──────────────────────────────────────────────────────────


def _coerce_result(payload: dict[str, Any]) -> tuple[str, str]:
    """Normalise raw LLM JSON into (role_family, seniority_level).

    Any value not in the valid sets is replaced with the safe fallback
    and a warning is logged.  Never raises.
    """
    raw_family = str(payload.get("role_family", "")).strip().lower().replace("-", "_")
    raw_seniority = str(payload.get("seniority_level", "")).strip().lower().replace("-", "_")

    if raw_family not in _VALID_ROLE_FAMILIES:
        logger.warning(
            "classifier.invalid_role_family",
            extra={"raw": raw_family, "fallback": _FALLBACK_ROLE_FAMILY},
        )
        raw_family = _FALLBACK_ROLE_FAMILY

    if raw_seniority not in _VALID_SENIORITY_LEVELS:
        logger.warning(
            "classifier.invalid_seniority",
            extra={"raw": raw_seniority, "fallback": _FALLBACK_SENIORITY},
        )
        raw_seniority = _FALLBACK_SENIORITY

    return raw_family, raw_seniority


# ── Gemini call ───────────────────────────────────────────────────────────────


async def classify_posting(
    jd_text: str,
    title: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Call Gemini to classify one posting.

    Returns ``(role_family, seniority_level)`` — both guaranteed to be
    valid enum members.  On any failure (network, parse, invalid enum
    value) logs the error and returns the safe fallback ``("other",
    "unknown")``.

    This function is the mock seam for tests::

        monkeypatch.setattr(
            "job_assist.services.classifier.classify_posting",
            async_stub,
        )
    """
    from google import genai
    from google.genai import types

    key = api_key if api_key is not None else settings.gemini_api_key
    if not key:
        raise RuntimeError("gemini_api_key is unset — cannot classify posting")

    used_model = model or _MODEL_NAME
    user_message = build_classify_prompt(title, jd_text)

    client = genai.Client(api_key=key)

    def _call() -> Any:
        return client.models.generate_content(
            model=used_model,
            contents=user_message,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                system_instruction=_SYSTEM_PROMPT,
            ),
        )

    response = await asyncio.to_thread(_call)
    raw = getattr(response, "text", None) or ""

    if not raw.strip():
        raise ValueError("Gemini returned an empty response for classify_posting")

    # Parse JSON — the mime type hint usually ensures clean JSON, but add
    # a fallback extraction in case prose wraps it.
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(raw[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"classify_posting: non-JSON response: {raw[:200]!r}") from exc
        else:
            raise ValueError(f"classify_posting: non-JSON response: {raw[:200]!r}") from None

    return _coerce_result(payload)
