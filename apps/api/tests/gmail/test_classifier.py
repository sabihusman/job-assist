"""Unit tests for the Gemini-backed classifier.

The ``EmailClassifier`` constructor would normally import ``google.genai``
and instantiate a real ``genai.Client``. For these tests we monkey-patch
the SDK out entirely with ``_FakeGenAi`` so we can exercise the JSON
parsing, normalisation, and rate-limit branches without ever calling
the model.

All emails here are synthetic — never real From addresses or bodies.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from job_assist.gmail.models import RawEmail

# ── Fake google.genai SDK ─────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    """Captures the prompt and replays a pre-seeded sequence of responses."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._responses: list[_FakeResponse | Exception] = []

    def queue(self, *responses: _FakeResponse | Exception) -> None:
        self._responses.extend(responses)

    def generate_content(self, *, model: str, contents: Any, config: Any) -> _FakeResponse:
        self.calls.append(contents)
        if not self._responses:
            return _FakeResponse('{"outcome_type":"unclassified","confidence":0.0,"reasoning":""}')
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, api_key: str) -> None:
        self.models = _FakeModels()


class _FakeGenAiHolder:
    """Yielded by the fixture so tests can reach the live ``_FakeClient`` *after*
    ``EmailClassifier(...)`` has been constructed — i.e. once the factory below
    has actually run and stashed the instance the classifier is bound to.
    """

    def __init__(self) -> None:
        self.client: _FakeClient | None = None

    @property
    def models(self) -> _FakeModels:
        assert self.client is not None, "EmailClassifier() must run before .models is read"
        return self.client.models


@pytest.fixture
def fake_genai() -> Iterator[_FakeGenAiHolder]:
    """Install a fake ``google.genai`` module for the duration of one test."""
    fake_genai_mod = types.ModuleType("genai")
    fake_types_mod = types.ModuleType("types")
    holder = _FakeGenAiHolder()

    def _client_factory(api_key: str) -> _FakeClient:
        c = _FakeClient(api_key)
        holder.client = c
        return c

    fake_genai_mod.Client = _client_factory  # type: ignore[attr-defined]

    class _Cfg:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_types_mod.GenerateContentConfig = _Cfg  # type: ignore[attr-defined]
    fake_genai_mod.types = fake_types_mod  # type: ignore[attr-defined]

    sys.modules["google.genai"] = fake_genai_mod
    sys.modules["google.genai.types"] = fake_types_mod

    # Patch the throttle so unit tests don't actually sleep 4s between calls.
    with patch("job_assist.gmail.classifier._MIN_REQUEST_GAP_S", 0.0):
        try:
            yield holder
        finally:
            sys.modules.pop("google.genai", None)
            sys.modules.pop("google.genai.types", None)


def _make_email(
    *,
    subject: str = "Re: Your application",
    from_address: str = "recruiter@example-company.com",
    body: str = "Thank you for applying.",
) -> RawEmail:
    return RawEmail(
        message_id="msg_test",
        from_address=from_address,
        from_domain=from_address.partition("@")[2],
        subject=subject,
        received_at=datetime(2026, 5, 1, tzinfo=UTC),
        body_text=body,
        snippet=body[:200],
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_happy_path_well_formed_json(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse(
            json.dumps(
                {
                    "outcome_type": "application_confirmation",
                    "confidence": 0.94,
                    "extracted_company": "Acmecorp",
                    "reasoning": "Body contains 'we received your application'.",
                }
            )
        )
    )

    result = await classifier.classify(_make_email(body="We received your application for SWE."))
    assert result.outcome_type == "application_confirmation"
    assert result.confidence == pytest.approx(0.94)
    assert result.extracted_company == "Acmecorp"


async def test_invalid_outcome_type_coerced_to_unclassified(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse('{"outcome_type":"made_up_category","confidence":0.8,"reasoning":""}')
    )
    result = await classifier.classify(_make_email())
    assert result.outcome_type == "unclassified"


async def test_non_json_response_yields_unclassified(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(_FakeResponse("Here is the answer: it's a rejection."))  # type: ignore[attr-defined]
    result = await classifier.classify(_make_email())
    assert result.outcome_type == "unclassified"
    assert result.confidence == 0.0


async def test_json_embedded_in_prose_is_recovered(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse(
            'Sure, here you go:\n```json\n{"outcome_type":"rejection_pre_screen",'
            '"confidence":0.9,"reasoning":"auto-rejection"}\n```'
        )
    )
    result = await classifier.classify(_make_email())
    assert result.outcome_type == "rejection_pre_screen"
    assert result.confidence == pytest.approx(0.9)


async def test_confidence_clamped_to_unit_interval(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse('{"outcome_type":"offer","confidence":1.7,"reasoning":""}')
    )
    result = await classifier.classify(_make_email())
    assert result.confidence == 1.0


async def test_429_triggers_retry_then_succeeds(fake_genai: _FakeGenAiHolder) -> None:
    """A 429-shaped error from the SDK is retried by tenacity."""
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        RuntimeError("429 RESOURCE_EXHAUSTED: rate limit exceeded"),
        _FakeResponse('{"outcome_type":"unrelated","confidence":0.95,"reasoning":""}'),
    )

    # Make tenacity's wait a no-op so the test stays fast.
    with patch("job_assist.gmail.classifier.wait_exponential", lambda **_: lambda *_: 0):
        result = await classifier.classify(_make_email())
    assert result.outcome_type == "unrelated"


async def test_non_429_error_propagates(fake_genai: _FakeGenAiHolder) -> None:
    """A non-rate-limit SDK error is NOT retried — propagates to caller."""
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(RuntimeError("500 internal error"))  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="500 internal error"):
        await classifier.classify(_make_email())


async def test_prompt_includes_email_metadata(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse('{"outcome_type":"unrelated","confidence":0.5,"reasoning":""}')
    )
    email = _make_email(
        subject="Welcome to our newsletter!",
        from_address="news@marketing.example.com",
        body="Check out our latest content.",
    )
    await classifier.classify(email)

    sent_prompt = fake_genai.models.calls[0]  # type: ignore[attr-defined]
    assert "Welcome to our newsletter!" in sent_prompt
    assert "news@marketing.example.com" in sent_prompt
    assert "Check out our latest content." in sent_prompt


async def test_body_truncated_to_2000_chars(fake_genai: _FakeGenAiHolder) -> None:
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse('{"outcome_type":"unrelated","confidence":0.5,"reasoning":""}')
    )
    long_body = "abcdef " * 1000  # > 2000 chars
    await classifier.classify(_make_email(body=long_body))
    sent = fake_genai.models.calls[0]  # type: ignore[attr-defined]
    # The prompt template embeds at most 2000 body chars between the
    # "Body (first 2000 chars):" header and the JSON-shape footer.
    body_section = sent.split("Body (first 2000 chars):\n", 1)[1].split("\n\nRespond", 1)[0]
    assert len(body_section) <= 2000


def test_module_has_known_outcome_types() -> None:
    """The prompt's outcome list must match the OutcomeType enum exactly."""
    from job_assist.db.enums import OutcomeType
    from job_assist.gmail.classifier import _OUTCOME_TYPES

    assert set(_OUTCOME_TYPES) == {o.value for o in OutcomeType}


def test_throttle_serialises_calls(fake_genai: _FakeGenAiHolder) -> None:
    """Two concurrent classify() calls must not interleave the throttle."""
    from job_assist.gmail.classifier import EmailClassifier

    classifier = EmailClassifier(api_key="test-key")
    fake_genai.models.queue(  # type: ignore[attr-defined]
        _FakeResponse('{"outcome_type":"unrelated","confidence":0.5,"reasoning":""}'),
        _FakeResponse('{"outcome_type":"unrelated","confidence":0.5,"reasoning":""}'),
    )

    async def run_two() -> None:
        await asyncio.gather(
            classifier.classify(_make_email()),
            classifier.classify(_make_email()),
        )

    asyncio.run(run_two())
    # Both calls landed at the SDK with the patched 0s gap.
    assert len(fake_genai.models.calls) == 2  # type: ignore[attr-defined]
