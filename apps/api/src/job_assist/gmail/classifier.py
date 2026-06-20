"""LLM-based outcome classifier for Gmail messages.

Uses Gemini 2.5 Flash Lite via the google-genai SDK. The model returns a
short JSON blob with the predicted ``outcome_type`` (one of OutcomeType),
a confidence score, an optional company name extracted from the body, and
a one-sentence rationale.

Rate-limit handling matches Gemini's free tier (15 RPM / 1500 RPD):
  * a baseline 4-second gap between requests gives a 15-RPM cap
  * any 429 the model throws back is retried with exponential backoff

Set ``temperature=0`` for deterministic output so the same email yields
the same classification across re-runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from job_assist.gmail.models import ClassificationResult, RawEmail
from job_assist.services.tracing import traceable

logger = logging.getLogger(__name__)

# Model + version identifiers — bump CLASSIFIER_VERSION whenever the prompt
# or model changes so re-classifications can be re-keyed in the future.
_MODEL_NAME = "gemini-2.5-flash-lite"
CLASSIFIER_VERSION = "gemini-flash-lite-v1"

# Free-tier baseline: 15 RPM => ~4s gap. Set higher to be safe under burst.
_MIN_REQUEST_GAP_S = 4.0

# Categories the prompt enumerates. Keep in sync with OutcomeType in
# db/enums.py — the validator below cross-checks against the enum.
_OUTCOME_TYPES = (
    "application_confirmation",
    "recruiter_screen_invite",
    "phone_interview_invite",
    "video_interview_invite",
    "onsite_interview_invite",
    "panel_interview_invite",
    "offer",
    "rejection_pre_screen",
    "rejection_post_screen",
    "rejection_post_interview",
    "withdrawn",
    "unrelated",
    "unclassified",
)

_PROMPT = """You are classifying job-search emails into one of these categories:

- application_confirmation: "Thank you for applying" / "We received your application"
- recruiter_screen_invite: Initial outreach for a phone/video screen
- phone_interview_invite, video_interview_invite, onsite_interview_invite, panel_interview_invite: Specific interview invitations (use the type that best matches)
- offer: A job offer
- rejection_pre_screen: Auto-rejection or rejection before any human interaction
- rejection_post_screen: Rejection after a screen or initial conversation
- rejection_post_interview: Rejection after a formal interview
- withdrawn: User withdrew their application
- unrelated: Not job-search related (newsletter, marketing, personal correspondence, etc.)
- unclassified: Job-related but doesn't fit any category clearly

Email:
From: {from_address}
Subject: {subject}
Body (first 2000 chars):
{body}

Respond ONLY with valid JSON in this exact shape:
{{
  "outcome_type": "<category>",
  "confidence": <0.0-1.0>,
  "extracted_company": "<company name or null>",
  "reasoning": "<one sentence>"
}}"""


def build_prompt(email: RawEmail) -> str:
    body = (email.body_text or email.snippet or "")[:2000]
    return _PROMPT.format(
        from_address=email.from_address,
        subject=email.subject,
        body=body,
    )


def _coerce_result(payload: dict[str, Any]) -> ClassificationResult:
    """Normalise loose LLM output before Pydantic validation."""
    outcome = str(payload.get("outcome_type", "")).strip().lower().replace("-", "_")
    if outcome not in _OUTCOME_TYPES:
        outcome = "unclassified"

    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    extracted_raw = payload.get("extracted_company")
    extracted = extracted_raw.strip() or None if isinstance(extracted_raw, str) else None

    reasoning = str(payload.get("reasoning", "")).strip()

    return ClassificationResult(
        outcome_type=outcome,
        confidence=confidence,
        extracted_company=extracted,
        reasoning=reasoning,
    )


class _RateLimited429(Exception):
    """Internal marker so tenacity retries only on 429-shaped errors."""


class EmailClassifier:
    """Classifies a :class:`RawEmail` into a :class:`ClassificationResult`."""

    def __init__(self, api_key: str, model: str = _MODEL_NAME) -> None:
        # Import lazily so unit tests that mock ``classify`` don't need the
        # google-genai package available.
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._last_request_ts: float = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request_ts
            if elapsed < _MIN_REQUEST_GAP_S:
                await asyncio.sleep(_MIN_REQUEST_GAP_S - elapsed)
            self._last_request_ts = time.monotonic()

    @retry(
        retry=retry_if_exception_type(_RateLimited429),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    async def _call_model(self, prompt: str) -> str:
        """Single Gemini call. Raises ``_RateLimited429`` for 429s so tenacity retries."""
        from google.genai import types

        def _call() -> Any:
            return self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:  # broad — the SDK raises various types for 429s
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                raise _RateLimited429(str(exc)) from exc
            raise

        text = getattr(response, "text", None)
        if not text:
            # Some SDK versions surface text on .candidates[0].content.parts[0].text
            candidates = getattr(response, "candidates", None) or []
            for cand in candidates:
                content = getattr(cand, "content", None)
                if content and getattr(content, "parts", None):
                    text = "".join(getattr(p, "text", "") for p in content.parts)
                    if text:
                        break
        return text or ""

    @traceable(run_type="llm", name="gemini.gmail_outcome_classify")
    async def classify(self, email: RawEmail) -> ClassificationResult:
        """Return the classifier's verdict for *email*.

        Never raises for malformed model output — returns ``unclassified``
        with ``confidence=0`` so the orchestrator can keep moving.
        """
        await self._throttle()
        prompt = build_prompt(email)
        raw = await self._call_model(prompt)
        if not raw.strip():
            logger.warning("classifier.empty_response", extra={"message_id": email.message_id})
            return ClassificationResult(
                outcome_type="unclassified",
                confidence=0.0,
                reasoning="empty model response",
            )

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # Try to peel a JSON object out of the response if the model wrapped
            # it in prose despite the response_mime_type request.
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                try:
                    payload = json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    logger.warning(
                        "classifier.json_parse_failed",
                        extra={"message_id": email.message_id, "raw": raw[:200]},
                    )
                    return ClassificationResult(
                        outcome_type="unclassified",
                        confidence=0.0,
                        reasoning="non-JSON response",
                    )
            else:
                return ClassificationResult(
                    outcome_type="unclassified",
                    confidence=0.0,
                    reasoning="non-JSON response",
                )

        # fix(audit): valid-but-non-object JSON (a top-level array or a bare
        # string — both observed with response_mime_type=application/json)
        # used to flow into _coerce_result and raise AttributeError on
        # ``payload.get``, violating this method's never-raises contract and
        # wasting the paid Gemini call. Unwrap a single-object array (the
        # common Gemini wrapping); anything else non-dict → unclassified.
        if isinstance(payload, list):
            payload = next((p for p in payload if isinstance(p, dict)), None)
        if not isinstance(payload, dict):
            logger.warning(
                "classifier.non_object_json",
                extra={"message_id": email.message_id, "raw": raw[:200]},
            )
            return ClassificationResult(
                outcome_type="unclassified",
                confidence=0.0,
                reasoning="non-object JSON response",
            )

        try:
            return _coerce_result(payload)
        except (ValidationError, AttributeError, TypeError):
            # _coerce_result clamps every field, so this is belt-and-braces —
            # but the contract is "never raises for malformed model output",
            # so any shape-level surprise degrades to unclassified.
            return ClassificationResult(
                outcome_type="unclassified",
                confidence=0.0,
                reasoning="validation failed",
            )
