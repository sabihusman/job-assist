"""Tests for the optional LangSmith tracing shim (Phase A4, Part 1).

These verify the HARD CONSTRAINTS, none of which need a network, a DB, or a
LangSmith key:

* the no-op shim works when ``langsmith`` is not installed (no ImportError),
* a decorated call behaves IDENTICALLY with tracing off (same result, and
  errors still propagate — tracing never swallows a sweep's exceptions),
* secrets/handles are scrubbed from captured inputs,
* a per-call ``langsmith_extra`` kwarg never leaks into the wrapped function.
"""

from __future__ import annotations

import sys

import pytest

from job_assist.services import tracing


def test_scrub_inputs_drops_handles_and_secrets() -> None:
    raw = {
        "self": object(),
        "api_key": "sk-secret",
        "session": object(),
        "client": object(),
        "gmail": object(),
        "classifier": object(),
        "jd_text": "Senior PM, fintech",
        "title": "Product Manager",
    }
    scrubbed = tracing.scrub_inputs(raw)
    assert scrubbed == {"jd_text": "Senior PM, fintech", "title": "Product Manager"}
    # No secret-bearing/unserializable handle survives.
    for k in ("self", "api_key", "session", "client", "gmail", "classifier"):
        assert k not in scrubbed


async def test_decorated_call_is_transparent_with_tracing_off() -> None:
    """With tracing inert (no env/key in tests), a decorated coroutine returns
    the same value and still raises — identical to undecorated behavior."""

    @tracing.traceable(run_type="llm", name="unit.echo")
    async def echo(x: int, *, api_key: str | None = None) -> int:
        return x * 2

    assert await echo(21) == 42

    @tracing.traceable(run_type="llm", name="unit.boom")
    async def boom() -> None:
        raise ValueError("kaboom")

    # Tracing must NOT swallow the error — the sweep's own try/except must still
    # see it, exactly as today.
    with pytest.raises(ValueError, match="kaboom"):
        await boom()


def _noop_factory(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Build the Layer-1 no-op decorator factory by making ``import langsmith``
    fail locally — without reloading the module or touching ``builtins`` (which
    would leak into other test files). ``sys.modules[name] = None`` makes the
    ``from langsmith import ...`` inside ``_build_impl`` raise ImportError, so it
    returns the no-op factory; ``monkeypatch.setitem`` reverts it afterwards.
    """
    monkeypatch.setitem(sys.modules, "langsmith", None)  # type: ignore[arg-type]
    return tracing._build_impl()


async def test_noop_shim_when_langsmith_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Layer 1: langsmith uninstalled → no-op decorator, no ImportError, and a
    stray ``langsmith_extra`` kwarg is stripped before the wrapped call."""
    factory = _noop_factory(monkeypatch)

    @factory(run_type="llm", name="unit.noop")
    async def fn(x: int) -> int:
        return x + 1

    # Identical behavior...
    assert await fn(1) == 2
    # ...and a call-site langsmith_extra must NOT reach fn (no TypeError).
    assert await fn(4, langsmith_extra={"metadata": {"posting_id": "abc"}}) == 5

    # Errors still propagate through the no-op path.
    @factory(run_type="llm", name="unit.noop_boom")
    async def boom() -> None:
        raise RuntimeError("still raised")

    with pytest.raises(RuntimeError, match="still raised"):
        await boom()


def test_noop_shim_supports_bare_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-op shim also supports the bare ``@traceable`` form (sync fn)."""
    factory = _noop_factory(monkeypatch)

    @factory
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
