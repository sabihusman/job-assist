"""Read-only client for the production API — used by the offline eval only.

The eval has no direct prod DB access; it reads through the authenticated public
read endpoints (same posture as the ops probe workflows). ``API_URL`` and
``API_AUTH_TOKEN`` come from the environment (GH Actions secrets). Nothing here
writes; nothing here imports ``openai``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

_PAGE = 100  # /postings hard-caps limit at 100


def _is_transient(exc: BaseException) -> bool:
    """Retry on Railway 5xx (the heavy uncapped /postings query 502s sometimes)
    and on transport errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _get_json(
    client: httpx.Client, url: str, params: dict[str, Any], headers: dict[str, str]
) -> Any:
    """GET + raise_for_status + .json(), retried on transient 5xx/transport."""
    resp = client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _base_and_headers() -> tuple[str, dict[str, str]]:
    base = os.environ.get("API_URL", "").rstrip("/")
    token = os.environ.get("API_AUTH_TOKEN", "")
    if not base:
        raise RuntimeError("API_URL is unset — eval reads prod through the API.")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return base, headers


def fetch_open_postings() -> list[dict[str, Any]]:
    """Page the FULL open-posting set (hard-rule-dropped + uncapped) read-only.

    ``include_filtered=true`` + ``per_company_cap=0`` defeats the triage view's
    gate/cap so we see every open row, each carrying role/seniority/salary and
    the computed ``state.resolved_status``.
    """
    base, headers = _base_and_headers()
    out: list[dict[str, Any]] = []
    offset = 0
    with httpx.Client(timeout=60) as client:
        while True:
            body = _get_json(
                client,
                f"{base}/postings",
                {
                    "include_filtered": "true",
                    "include_closed": "false",
                    "per_company_cap": 0,
                    "limit": _PAGE,
                    "offset": offset,
                },
                headers,
            )
            items = body.get("items", [])
            out.extend(items)
            total = int(body.get("total", 0))
            offset += _PAGE
            if offset >= total or not items:
                break
    return out


def fetch_posting_detail(posting_id: str) -> dict[str, Any]:
    """GET /postings/{id} → the JD detail. ``description_markdown`` is jd_text.

    Returns ``{"title": ..., "jd_text": ...}`` — the exact input used for BOTH
    the OpenAI pre-label and the Phase-3 Gemini re-score (identical input).
    """
    base, headers = _base_and_headers()
    with httpx.Client(timeout=60) as client:
        body = _get_json(client, f"{base}/postings/{posting_id}", {}, headers)
    role = body.get("role") or {}
    return {
        "title": role.get("title") or body.get("title") or "",
        "jd_text": body.get("description_markdown") or "",
    }


def fetch_outcomes(*, job_related: bool, max_rows: int = 4000) -> list[dict[str, Any]]:
    """Page /outcomes → outcome rows (id, stage, subject, raw_snippet, ...).

    ``job_related=true`` excludes unrelated/unclassified (the ~197 lifecycle
    rows). ``job_related=false`` returns everything (for negative controls).
    """
    base, headers = _base_and_headers()
    out: list[dict[str, Any]] = []
    offset = 0
    page = 200
    with httpx.Client(timeout=60) as client:
        while len(out) < max_rows:
            body = _get_json(
                client,
                f"{base}/outcomes",
                {
                    "job_related": "true" if job_related else "false",
                    "limit": page,
                    "offset": offset,
                },
                headers,
            )
            items = body.get("items", [])
            out.extend(items)
            total = int(body.get("total", 0))
            offset += page
            if offset >= total or not items:
                break
    return out


def fetch_outcome_type_breakdown() -> dict[str, int]:
    """Outcome_type counts from the read-only outcome-linking diagnostic.

    The live endpoint returns the q1-q4 resume-coverage shape, whose
    ``q2_by_outcome_type`` is the per-type breakdown (``{outcome_type, total,
    linked, pct_linked}``). Fall back to ``by_outcome_type`` for forward-compat.
    """
    base, headers = _base_and_headers()
    with httpx.Client(timeout=60) as client:
        resp = client.get(f"{base}/admin/diagnostics/outcome-linking", headers=headers)
        resp.raise_for_status()
        body = resp.json()
    rows = body.get("q2_by_outcome_type") or body.get("by_outcome_type") or []
    counts: dict[str, int] = {}
    for row in rows:
        ot = row.get("outcome_type")
        if ot is None:
            continue
        if "total" in row:
            n = int(row["total"])
        else:
            n = int(row.get("linked_to_company", 0)) + int(row.get("unlinked", 0))
        counts[str(ot)] = n
    return counts
