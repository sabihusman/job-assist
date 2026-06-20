"""Optional LangSmith tracing for the Gemini call sites (Phase A4, Part 1).

Triple-gated OFF by default — three independent layers, any one of which
keeps the app running EXACTLY as today:

* **Layer 1 — not installed.** If ``langsmith`` isn't importable, ``traceable``
  here is a no-op decorator. No ImportError ever reaches a caller.
* **Layer 2 — installed but disabled.** If ``LANGSMITH_TRACING`` is not
  ``"true"`` in the environment, the real ``@traceable`` is inert — the SDK's
  own native behavior (it checks the env per call and runs the function
  directly, exporting nothing). No key needed; local/CI default is OFF.
* **Layer 3 — enabled.** With ``LANGSMITH_TRACING=true`` + ``LANGSMITH_API_KEY``
  set (Railway env only), runs are batched and exported on a background
  thread. Export failures are swallowed by the SDK and never propagate into a
  sweep, so tracing adds no new failure mode and no hot-path latency.

This module imports nothing heavy and reads no secrets. The API key is supplied
to the SDK purely through the environment in production — never from code.

Input scrubbing
---------------
Every decoration drops non-serializable handles and anything secret-bearing
(DB sessions, the genai client, API keys, the Gmail client/classifier) from the
captured inputs, so traces carry prompts/results — never credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])

# Arg names dropped from every traced input dict: unserializable handles and
# anything that could carry a secret (api keys, the genai/Gmail clients, the DB
# session). Matched by parameter name across all decorated call sites.
_SCRUB_KEYS = frozenset(
    {
        "self",
        "cls",
        "api_key",
        "session",
        "db",
        "client",
        "gmail",
        "classifier",
        "on_progress",
        "credentials",
        "token",
        "runtime",
    }
)


def scrub_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Drop handles/secrets from a traced inputs dict (see ``_SCRUB_KEYS``)."""
    return {k: v for k, v in inputs.items() if k not in _SCRUB_KEYS}


def _build_impl() -> Callable[..., Any]:
    """Resolve the active decorator factory once, at import time.

    Returns the real ``langsmith.traceable`` (wrapped to default input
    scrubbing) when langsmith is importable, else a no-op decorator factory.
    """
    try:
        from langsmith import traceable as _ls_traceable
    except Exception:
        import asyncio
        import functools

        def _noop_impl(*d_args: Any, **d_kwargs: Any) -> Any:
            def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
                if asyncio.iscoroutinefunction(fn):

                    @functools.wraps(fn)
                    async def _async_wrapper(*a: Any, **k: Any) -> Any:
                        k.pop("langsmith_extra", None)
                        return await fn(*a, **k)

                    return _async_wrapper

                @functools.wraps(fn)
                def _sync_wrapper(*a: Any, **k: Any) -> Any:
                    k.pop("langsmith_extra", None)
                    return fn(*a, **k)

                return _sync_wrapper

            # Bare form: @traceable
            if len(d_args) == 1 and callable(d_args[0]) and not d_kwargs:
                return _wrap(d_args[0])
            # Parametrized form: @traceable(run_type="llm", name="...")
            return _wrap

        return _noop_impl

    def _ls_impl(*args: Any, **kwargs: Any) -> Any:
        is_bare = len(args) == 1 and callable(args[0]) and not kwargs
        if not is_bare:
            kwargs.setdefault("process_inputs", scrub_inputs)
        return _ls_traceable(*args, **kwargs)

    return _ls_impl


_impl = _build_impl()


def traceable(*args: Any, **kwargs: Any) -> Callable[[F], F]:
    """Signature-preserving facade over the active decorator factory.

    Used everywhere as the parametrized form (``@traceable(run_type="llm",
    name="...")``), so the return type is a decorator that preserves the
    wrapped function's signature — keeping decorated call sites fully typed.
    """
    return cast("Callable[[F], F]", _impl(*args, **kwargs))


__all__ = ["scrub_inputs", "traceable"]
