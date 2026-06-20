"""OpenAI pre-labeler — the ONLY module in the repo that imports ``openai``.

Independent ground-truth proposer for the A4 eval (Gemini is the model under
test; OpenAI is a different vendor/lineage → a genuine second opinion). Runs
OFFLINE ONLY. The API key is read from the environment at call time and is a
Railway-env / GitHub-Actions secret — never committed.

Model: an o-series REASONING model (default ``o3``), chosen because the eval's
whole point is catching seniority under-leveling — exactly the nuanced-judgment
case reasoning models handle best. The exact served model id is recorded in
every artifact (``response.model``) so results are reproducible regardless of
which dated snapshot the alias resolves to. Override with ``EVAL_OPENAI_MODEL``.

o-series note: reasoning models only accept the default temperature, so a
``temperature=0`` request 400s. We attempt ``temperature=0`` (the deterministic
intent) and transparently fall back to the model default if the API rejects it,
recording which applied in ``temperature_mode``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from job_assist.eval.prompts import (
    EMAIL_PROMPT_VERSION,
    EMAIL_SYSTEM_PROMPT,
    JD_PROMPT_VERSION,
    JD_SYSTEM_PROMPT,
    build_email_user_prompt,
    build_jd_user_prompt,
    email_response_schema,
    jd_response_schema,
)

DEFAULT_MODEL = "o3"


def _model_id() -> str:
    return os.environ.get("EVAL_OPENAI_MODEL", DEFAULT_MODEL)


@dataclass(frozen=True)
class LabelResult:
    """One pre-label plus the provenance recorded into every artifact."""

    label: dict[str, Any]
    served_model: str
    prompt_version: str
    temperature_mode: str  # "zero" | "model_default"


def _client() -> Any:
    """Construct the OpenAI client, reading the key from the environment.

    Import is local so ``openai`` is only ever loaded inside this offline
    module — never at production import time.
    """
    from openai import OpenAI

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is unset — the eval pre-labeler runs offline only "
            "with the key supplied via the environment (Railway/GH Actions secret)."
        )
    return OpenAI(api_key=key)


def _complete(
    client: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """One structured-output completion. Returns (parsed, served_model, temp_mode).

    Attempts temperature=0 first (deterministic intent); on an "unsupported
    temperature" rejection (o-series), retries with the model default.
    """
    kwargs: dict[str, Any] = {
        "model": _model_id(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_schema", "json_schema": schema},
    }
    try:
        resp = client.chat.completions.create(temperature=0, **kwargs)
        temp_mode = "zero"
    except Exception as exc:  # narrow on the temperature signal, re-raise else
        if "temperature" not in str(exc).lower():
            raise
        resp = client.chat.completions.create(**kwargs)
        temp_mode = "model_default"

    content = resp.choices[0].message.content or "{}"
    return json.loads(content), resp.model, temp_mode


def label_jd(client: Any, *, title: str, jd_text: str) -> LabelResult:
    """Pre-label one JD → {role_family, seniority_level, rationale}."""
    parsed, served, temp_mode = _complete(
        client,
        system_prompt=JD_SYSTEM_PROMPT,
        user_prompt=build_jd_user_prompt(title, jd_text),
        schema=jd_response_schema(),
    )
    return LabelResult(
        label=parsed,
        served_model=served,
        prompt_version=JD_PROMPT_VERSION,
        temperature_mode=temp_mode,
    )


def label_email(client: Any, *, subject: str, body: str) -> LabelResult:
    """Pre-label one email → {outcome_type, rationale}."""
    parsed, served, temp_mode = _complete(
        client,
        system_prompt=EMAIL_SYSTEM_PROMPT,
        user_prompt=build_email_user_prompt(subject, body),
        schema=email_response_schema(),
    )
    return LabelResult(
        label=parsed,
        served_model=served,
        prompt_version=EMAIL_PROMPT_VERSION,
        temperature_mode=temp_mode,
    )


def new_client() -> Any:
    """Public constructor used by the offline runner."""
    return _client()
