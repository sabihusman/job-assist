"""Phase 3 — score the production Gemini classifier vs verified ground truth.

LOCAL ONLY. Reads verified_labels.jsonl (real JD/email text), runs the UNCHANGED
production classifier per row on the SAME input bytes (input_sha256 lock), and
aggregates Gemini's accuracy vs the verified labels — alongside o3's accuracy on
the same rows (three-way: Gemini vs o3 vs human-verified).

The classifier callables are INJECTED (run.py wires the real async classifiers;
tests pass stubs), so the aggregation is fully testable with no Gemini calls.
Nothing here changes the production classifier — it only observes its output.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

# PM career ladder, ascending. ``unknown`` / None / non-ladder → rank -1, so it
# sorts below every real level (used for the under-leveling tally).
SENIORITY_LADDER: tuple[str, ...] = (
    "intern",
    "apm",
    "pm",
    "senior_pm",
    "lead_pm",
    "principal_pm",
)

ClassifyJd = Callable[[str, str], Awaitable[tuple[Any, Any]]]
ClassifyEmail = Callable[[str, str], Awaitable[Any]]


def _norm(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _input_sha256(payload: dict[str, Any]) -> str:
    """Identical to run._sha256 / verify._input_sha256 — the identical-input lock."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _rank(s: str | None) -> int:
    return SENIORITY_LADDER.index(s) if s in SENIORITY_LADDER else -1


async def collect(
    rows: list[dict[str, Any]],
    *,
    classify_jd: ClassifyJd,
    classify_email: ClassifyEmail,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the (injected) classifier per row. Enforces the input_sha256 lock:
    a row whose recomputed hash != stored hash is SKIPPED + reported, never
    silently scored on different bytes.
    """
    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in rows:
        inp = r.get("input") or {}
        if _input_sha256(inp) != r.get("input_sha256"):
            skipped.append(
                {"id": r.get("id"), "kind": r.get("kind"), "reason": "input_sha256_mismatch"}
            )
            continue
        kind = r.get("kind")
        if kind == "jd":
            g_rf, g_sen = await classify_jd(inp.get("title") or "", inp.get("jd_text") or "")
            scored.append(
                {
                    "kind": "jd",
                    "id": r.get("id"),
                    "eligible": bool(r.get("seniority_eval_eligible")),
                    "verified": r.get("verified_label") or {},
                    "o3": r.get("o3_label") or {},
                    "gemini": {"role_family": _norm(g_rf), "seniority_level": _norm(g_sen)},
                }
            )
        elif kind == "email":
            g_ot = await classify_email(inp.get("subject") or "", inp.get("raw_snippet") or "")
            scored.append(
                {
                    "kind": "email",
                    "id": r.get("id"),
                    "verified": r.get("verified_label") or {},
                    "o3": r.get("o3_label") or {},
                    "gemini": {"outcome_type": _norm(g_ot)},
                }
            )
    return scored, skipped


def _acc(rows: list[dict[str, Any]], model: str, field: str) -> dict[str, Any]:
    correct = sum(1 for s in rows if _norm(s[model].get(field)) == _norm(s["verified"].get(field)))
    n = len(rows)
    return {"correct": correct, "n": n, "accuracy": round(correct / n, 4) if n else None}


def _confusion(rows: list[dict[str, Any]], field: str, model: str) -> dict[str, dict[str, int]]:
    c: dict[str, dict[str, int]] = {}
    for s in rows:
        t = str(_norm(s["verified"].get(field)))
        p = str(_norm(s[model].get(field)))
        c.setdefault(t, {}).setdefault(p, 0)
        c[t][p] += 1
    return c


def _under_leveled(rows: list[dict[str, Any]], model: str) -> int:
    """Rows where the model's seniority ranks BELOW the verified level (incl.
    unknown/None, which ranks -1) — the under-leveling tally."""
    return sum(
        1
        for s in rows
        if _rank(_norm(s[model].get("seniority_level")))
        < _rank(_norm(s["verified"].get("seniority_level")))
    )


def aggregate(
    scored: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    profile_context_used: bool,
) -> dict[str, Any]:
    """Three-way accuracy summary: Gemini vs o3 vs verified, per dimension."""
    jd = [s for s in scored if s["kind"] == "jd"]
    em = [s for s in scored if s["kind"] == "email"]

    rf_rows = [s for s in jd if _norm(s["verified"].get("role_family"))]
    sen_rows = [s for s in jd if s["eligible"] and _norm(s["verified"].get("seniority_level"))]
    ot_rows = [s for s in em if _norm(s["verified"].get("outcome_type"))]

    return {
        "model": "gemini production classifier (classify_posting / GmailOutcomeClassifier)",
        "profile_context_used": profile_context_used,
        "n_scored": len(scored),
        "n_skipped": len(skipped),
        "skipped": skipped,
        "role_family": {
            "n": len(rf_rows),
            "gemini": _acc(rf_rows, "gemini", "role_family"),
            "o3": _acc(rf_rows, "o3", "role_family"),
            "confusion_verified_to_gemini": _confusion(rf_rows, "role_family", "gemini"),
        },
        "seniority": {
            "n_eligible": len(sen_rows),
            "gemini": _acc(sen_rows, "gemini", "seniority_level"),
            "o3": _acc(sen_rows, "o3", "seniority_level"),
            "gemini_under_leveled": _under_leveled(sen_rows, "gemini"),
            "o3_under_leveled": _under_leveled(sen_rows, "o3"),
            "confusion_verified_to_gemini": _confusion(sen_rows, "seniority_level", "gemini"),
        },
        "outcome_type": {
            "n": len(ot_rows),
            "gemini": _acc(ot_rows, "gemini", "outcome_type"),
            "o3": _acc(ot_rows, "o3", "outcome_type"),
            "confusion_verified_to_gemini": _confusion(ot_rows, "outcome_type", "gemini"),
        },
    }
