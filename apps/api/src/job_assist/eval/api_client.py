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

_PAGE = 100  # /postings hard-caps limit at 100


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
            resp = client.get(
                f"{base}/postings",
                params={
                    "include_filtered": "true",
                    "include_closed": "false",
                    "per_company_cap": 0,
                    "limit": _PAGE,
                    "offset": offset,
                },
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
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
        resp = client.get(f"{base}/postings/{posting_id}", headers=headers)
        resp.raise_for_status()
        body = resp.json()
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
            resp = client.get(
                f"{base}/outcomes",
                params={
                    "job_related": "true" if job_related else "false",
                    "limit": page,
                    "offset": offset,
                },
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
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
