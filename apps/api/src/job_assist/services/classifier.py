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
  v2 (gemini)     — Gemini Flash Lite, "aggressive fit" prompt. Over-assigned
                    ``product_management``: ~33% of PM-tagged rows were
                    actually engineers / designers / IT / ops / CSM, scoring
                    88-100 and bypassing the scorer's role_family gate
                    (Bestiary 5.21). The recall win cost precision.
  v3 (this file)  — precision-tightened prompt. Explicit DISCRIMINATOR (owns
                    a roadmap vs builds/designs/supports/analyzes) + NEGATIVE
                    criteria + negative few-shots. ``other`` reframed as the
                    correct answer for most non-PM roles, not a last resort.

Prompt design
─────────────
The prompt prioritizes PRECISION over fit:
  * Enumerate ALL 5 values explicitly so the model never invents a 6th.
  * A single DISCRIMINATOR question separates ownership (PM) from
    build/design/support/analyze (not PM) — a "Product" in the title does
    NOT imply product_management (Product Designer, Product Operations).
  * Explicit NEGATIVE criteria + negative few-shots for the roles the v2
    prompt mislabeled: engineers, designers, IT/DevOps, ops-managers,
    Customer Success, analysts, eng-managers.
  * The model is told NOT to force a PM bucket; ``other`` is correct for
    the majority of non-PM roles.

For seniority: ``unknown`` when the JD has no level signal OR the role is
not on the PM ladder (role_family = other).

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

# v5 (feat/strategy-spine): adds the ``strategy_ops`` family — Strategy & Ops /
# Corporate Strategy / BizOps / Chief of Staff at warm-path employers. Bumping
# the version makes the reclassify sweep revisit existing rows, so strategy
# roles previously bucketed other/program_management self-heal into the new
# family.
CLASSIFIER_VERSION = "gemini-flash-lite-v5"
_MODEL_NAME = "gemini-2.5-flash-lite"

# Valid enum values — kept in sync with db/enums.py. The defensive parser
# below rejects any value not in these sets and falls back to the safe default.
_VALID_ROLE_FAMILIES = frozenset(
    {
        "product_management",
        "product_owner",
        "product_marketing",
        "program_management",
        "strategy_ops",
        "other",
    }
)
_VALID_SENIORITY_LEVELS = frozenset(
    {"intern", "apm", "pm", "senior_pm", "lead_pm", "principal_pm", "unknown"}
)

_FALLBACK_ROLE_FAMILY = "other"
_FALLBACK_SENIORITY = "unknown"

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are classifying job postings for a PM job-search tool. Assign EXACTLY
ONE value for each of two dimensions. Accuracy matters more than fit: when a
role is genuinely NOT a product-management-ladder role, classify it as
``other`` (or the correct adjacent bucket). Do NOT force a posting into a PM
bucket — a precise ``other`` is far better than a wrong ``product_management``.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIMENSION 1 — role_family
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Choose one of these SIX values. Do not invent a seventh.

THE DISCRIMINATOR: does the role OWN a product — its strategy, roadmap,
discovery, and prioritization for a product or feature area — and decide
WHAT to build and WHY? That is a Product Manager. Roles that BUILD, DESIGN,
SUPPORT, SELL, or ANALYZE someone else's product are NOT product managers,
even when their title contains the word "Product".

  product_management  — OWNS product strategy / roadmap / discovery for a
                        product or feature area; decides what to build and
                        why; works with engineering + design as the
                        decision-maker. Includes "Product Manager", "Senior/
                        Group/Principal PM", "Growth PM", "AI PM", "Platform
                        PM", "Head of Product". The title almost always
                        contains "Product Manager" or "Head/VP/Director of
                        Product".
  product_owner       — Agile PO role, backlog management, sprint-level
                        scope. Usually titled "Product Owner".
  product_marketing   — go-to-market, messaging, positioning, launches.
                        Includes "PMM", "Product Marketing Manager",
                        "Senior PMM".
  program_management  — cross-functional coordination / operations / process
                        WITHOUT owning a product roadmap. Includes "TPM",
                        "Technical Program Manager", "Program Manager",
                        "Product Operations", "AI Operations", and other
                        "... Operations" or ops-manager roles whose JD is
                        about process + coordination rather than product
                        ownership.
  strategy_ops        — corporate / business STRATEGY and strategy-execution
                        roles: shapes business- or org-level strategy, runs
                        internal strategy projects, or operates the executive
                        cadence. Includes "Strategy & Operations Manager",
                        "Corporate Strategy", "Business Strategy Manager",
                        "Business Operations" / "BizOps" (strategy-flavored),
                        in-house "Strategy Consultant", "Chief of Staff".
                        THE STRATEGY TEST: the role advises or decides on
                        BUSINESS direction (markets, growth initiatives,
                        operating model) — NOT a product roadmap (that is
                        product_management) and NOT day-to-day operations
                        execution or process coordination (that is
                        program_management). A bare "Operations" title with
                        no strategy scope is NOT strategy_ops.
  other               — anything that is not one of the five above. This is
                        the CORRECT answer for the majority of non-PM roles,
                        not a last resort.

NEGATIVE CRITERIA — these are NOT product_management. Classify as shown:
  • Software / AI / ML / data / infrastructure / platform ENGINEERS
    (any "Engineer" title without "Product Manager", incl. "Design
    Engineer", "Applied AI Engineer", "Prompt Engineer") → other
  • DESIGNERS of every kind, INCLUDING "Product Designer", "Staff Product
    Designer", "UX/UI Designer" → other  (design is not product management)
  • "Product Operations", "AI Operations", "Operations Manager", and other
    ops / coordination roles → program_management  (operations, not product
    ownership)
  • IT / DevOps / SRE / Security engineers ("Senior IT Engineer", etc.) → other
  • Customer Success / Account Management / Sales roles ("Customer Success
    Manager", "Account Executive") → other
  • Data / BI / performance ANALYSTS ("Performance Analyst", "Data
    Analyst") → other
  • Content / video / brand / creative roles → other (or product_marketing
    only if the JD is genuinely go-to-market / messaging)
  • Engineering MANAGERS (manage engineers, not a product roadmap) → other
A title containing "Product" (Product Designer, Product Operations, Product
Engineer) is NOT automatically product_management — apply the discriminator.

NEGATIVE CRITERIA for strategy_ops — these are NOT strategy_ops:
  • Generic operations EXECUTION roles — "Operations Manager", "Plant
    Operations Manager", "Warehouse / Clinical / Network Operations" — have
    no business-strategy scope → program_management (or other for site /
    facilities execution), per the rules above.
  • "IT Project Manager", construction / facilities project managers →
    other (delivery coordination, not business strategy).
  • "Sales Operations" / "Revenue Operations" ANALYST roles about CRM and
    reporting hygiene → other.
A title containing "Operations" is NOT automatically strategy_ops — apply
THE STRATEGY TEST (business direction vs execution).

FEW-SHOT EXAMPLES (role_family):
  POSITIVE:
  "Senior PMM, Growth" + JD about messaging → product_marketing
  "TPM, Infrastructure" + JD about cross-team coordination → program_management
  "Growth PM" + JD about A/B tests and funnel metrics → product_management
  "AI Product Strategist" + JD about owning the ML platform roadmap → product_management
  "Product Owner" + JD about sprint backlog → product_owner
  "Strategy & Operations Manager" + JD about growth initiatives → strategy_ops
  "Manager, Corporate Strategy" + JD about market analysis for the exec team → strategy_ops
  "Business Operations Lead (BizOps)" + JD about cross-functional strategy projects → strategy_ops
  "Chief of Staff to the COO" + JD about operating cadence + strategic initiatives → strategy_ops
  "Strategy Consultant, Internal Strategy Group" + JD about corporate growth strategy → strategy_ops
  NEGATIVE (do NOT mislabel these as product_management OR strategy_ops):
  "Operations Manager" + JD about day-to-day site operations → program_management
  "Plant Operations Manager" + JD about manufacturing lines / safety → other
  "IT Project Manager" + JD about infrastructure delivery timelines → other
  "Sales Operations Analyst" + JD about CRM hygiene / reporting → other
  "Senior Product Designer" + JD about design systems / Figma → other
  "Design Engineer, Brand" + JD about building UI → other
  "Software Engineer, AI Workflows" + JD about writing code → other
  "Applied AI Engineer" / "Prompt Engineer" + JD about model/eval work → other
  "AI Operations Manager" + JD about running ops / agentic workflows → program_management
  "Product Operations Specialist" + JD about process + tooling, no roadmap → program_management
  "Senior IT Engineer, Enterprise Systems" + JD about internal IT → other
  "Customer Success Manager, Strategic" + JD about account retention → other
  "CX Automation Performance Analyst" + JD about dashboards / metrics → other
  "Engineering Manager" + JD about managing an eng team → other
  "Video Lead, Stories" + JD about producing video content → other

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIMENSION 2 — seniority_level
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Choose one of these SEVEN values along the PM career ladder. If role_family
is ``other`` and the JD has no clear level signal, ``unknown`` is fine — do
not invent a level for a non-PM role.

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
  unknown       — the JD contains NO level signal whatsoever, OR the role
                  is not on the PM ladder (role_family = other)

FEW-SHOT EXAMPLES (seniority_level):
  "Senior PM" → senior_pm
  "Product Manager" (no qualifier, 3-5 yrs exp) → pm
  "Associate Product Manager" → apm
  "Lead Product Manager" → lead_pm
  "Principal PM" → principal_pm
  "PM Intern" → intern
  "Product Manager" with JD saying "10+ years" → senior_pm or lead_pm
    (pick based on scope described)
  Non-PM role (role_family = other) → unknown

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — respond ONLY with this JSON, no prose:
{"role_family": "<value>", "seniority_level": "<value>"}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ── Prompt builder ────────────────────────────────────────────────────────────


def build_profile_context(
    looking_for_text: str | None,
    role_keywords: list[str] | None,
) -> str | None:
    """Build the operator-context block from the profile's free-form targets.

    DISAMBIGUATION context only — it nudges genuinely borderline titles toward
    the operator's stated domain. It must NOT override the precision-first
    discriminator or be used to suppress a role into the wrong family
    (preferences belong in the scorer, where the role_family cap-at-40 already
    ranks down non-PM families). Returns ``None`` when the profile carries no
    text/keywords, so the prompt is byte-identical to the no-profile path.
    """
    text = (looking_for_text or "").strip()
    kws = [k.strip() for k in (role_keywords or []) if k and k.strip()]
    if not text and not kws:
        return None
    lines: list[str] = []
    if text:
        # Bound the injected text so token cost stays predictable.
        lines.append(f"Targets: {text[:600]}")
    if kws:
        lines.append(f"Keywords: {', '.join(kws)}")
    return "\n".join(lines)


def build_classify_prompt(
    title: str,
    jd_text: str,
    profile_context: str | None = None,
) -> str:
    """Build the user-turn message for a classify call.

    Kept as a pure function so unit tests can assert the title, JD-text, and
    (when present) the operator context appear in the prompt without needing a
    Gemini call.
    """
    # Truncate JD to keep token cost bounded.  The first 3000 chars
    # carry essentially all the signal needed for family + seniority.
    jd_snippet = (jd_text or "").strip()[:3000]
    base = f"Title: {title.strip()}\n\nJob description:\n{jd_snippet}"
    if not profile_context:
        return base
    # Disambiguation only — never an override or a suppression instruction.
    return (
        f"{base}\n\n"
        "OPERATOR CONTEXT (use ONLY to disambiguate genuinely borderline titles "
        "toward the operator's domain; do NOT override the discriminator, force "
        "a PM bucket, or suppress a role into the wrong family — apply the same "
        f"taxonomy):\n{profile_context}"
    )


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
    profile_context: str | None = None,
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
    user_message = build_classify_prompt(title, jd_text, profile_context)

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
