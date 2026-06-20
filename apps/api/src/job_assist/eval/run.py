"""Offline eval runner (CLI) — NEVER a route/cron/sweep.

Modes:
  * ``count``    — read-only: pull prod counts for sample sizing, print JSON,
                   and write a counts artifact under ``eval/datasets/``.
  * ``generate`` — run the OpenAI pre-labeler over the confirmed sample and
                   write the pre-label JSONL. Gated: requires the sample to be
                   confirmed (Phase 1 review) and OPENAI_API_KEY in the env.

Run via the ``eval-prelabel`` workflow_dispatch (which injects API_URL /
API_AUTH_TOKEN / OPENAI_API_KEY) or locally with those env vars set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_assist.eval.api_client import (
    fetch_open_postings,
    fetch_outcome_type_breakdown,
    fetch_outcomes,
    fetch_posting_detail,
)
from job_assist.eval.sample import compute_counts, select_email_sample, select_jd_sample

DATASETS_DIR = Path(__file__).parent / "datasets"


def _sha256(payload: dict[str, Any]) -> str:
    """Stable hash of the exact model input — Phase 3 asserts identical input."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _dedup_by_id(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for lst in lists:
        for row in lst:
            rid = str(row.get("id"))
            if rid not in seen:
                seen.add(rid)
                out.append(row)
    return out


def _run_count(stamp: str) -> int:
    """Read-only count step. Prints JSON and writes a timestamped artifact."""
    postings = fetch_open_postings()
    counts = compute_counts(postings)
    counts["by_outcome_type"] = fetch_outcome_type_breakdown()
    counts["generated_at"] = stamp

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    out = DATASETS_DIR / f"pool_counts.{stamp}.json"
    out.write_text(json.dumps(counts, indent=2, sort_keys=True), encoding="utf-8")

    # Print to stdout so the workflow log carries the numbers.
    print(json.dumps(counts, indent=2, sort_keys=True))
    print(f"\n[count] wrote {out}", file=sys.stderr)
    return 0


def _run_generate(stamp: str) -> int:
    """Pre-label the confirmed stratified sample with the o-series model.

    Identical-input lock: JDs use ``description_markdown``; emails use
    ``subject`` + ``raw_snippet`` — the SAME text Phase-3 Gemini must re-score,
    captured verbatim with a sha256 so the comparison measures model difference,
    not input difference. Per-item failures are collected, not fatal.
    """
    from job_assist.eval.openai_labeler import label_email, label_jd, new_client

    client = new_client()
    labeled_at = datetime.now(UTC).isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    jd_dist: Counter[str] = Counter()
    sen_dist: Counter[str] = Counter()
    email_dist: Counter[str] = Counter()
    # Q3 sanity: for the hard-seniority-mismatch stratum, capture title +
    # Gemini's (production) seniority + o3's seniority side by side.
    mismatch_detail: list[dict[str, Any]] = []

    # ── JDs ──────────────────────────────────────────────────────────────────
    jd_sample = select_jd_sample(fetch_open_postings())
    for item in jd_sample:
        pid = str(item.get("id"))
        try:
            detail = fetch_posting_detail(pid)
            model_input = {"title": detail["title"], "jd_text": detail["jd_text"]}
            res = label_jd(client, title=detail["title"], jd_text=detail["jd_text"])
            records.append(
                {
                    "kind": "jd",
                    "id": pid,
                    "stratum": item.get("_stratum"),
                    "input": model_input,
                    "input_sha256": _sha256(model_input),
                    "openai_label": res.label,
                    "model_id": res.served_model,
                    "prompt_version": res.prompt_version,
                    "temperature_mode": res.temperature_mode,
                    "generated_at": stamp,
                    "labeled_at": labeled_at,
                }
            )
            jd_dist[str(res.label.get("role_family"))] += 1
            sen_dist[str(res.label.get("seniority_level"))] += 1
            if item.get("_stratum") == "hard_seniority_mismatch":
                role = item.get("role") or {}
                mismatch_detail.append(
                    {
                        "title": role.get("title"),
                        "gemini_seniority": role.get("seniority"),
                        "o3_seniority": res.label.get("seniority_level"),
                        "o3_role_family": res.label.get("role_family"),
                    }
                )
        except Exception as exc:  # collect, never abort the batch
            errors.append({"kind": "jd", "id": pid, "error": str(exc)[:300]})

    # ── Emails (identical subject+raw_snippet input) ─────────────────────────
    lifecycle = fetch_outcomes(job_related=True)
    negatives = fetch_outcomes(job_related=False, max_rows=800)
    email_sample = select_email_sample(_dedup_by_id(lifecycle, negatives))
    for o in email_sample:
        oid = str(o.get("id"))
        try:
            subject = o.get("subject") or ""
            snippet = o.get("raw_snippet") or ""
            model_input = {"subject": subject, "raw_snippet": snippet}
            res = label_email(client, subject=subject, body=snippet)
            records.append(
                {
                    "kind": "email",
                    "id": oid,
                    "stratum": o.get("_stratum"),
                    "production_outcome_type": o.get("stage"),
                    "input": model_input,
                    "input_sha256": _sha256(model_input),
                    "openai_label": res.label,
                    "model_id": res.served_model,
                    "prompt_version": res.prompt_version,
                    "temperature_mode": res.temperature_mode,
                    "generated_at": stamp,
                    "labeled_at": labeled_at,
                }
            )
            email_dist[str(res.label.get("outcome_type"))] += 1
        except Exception as exc:
            errors.append({"kind": "email", "id": oid, "error": str(exc)[:300]})

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    out = DATASETS_DIR / f"prelabels.{stamp}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "jd_labeled": jd_dist.total(),
        "email_labeled": email_dist.total(),
        "total_labeled": len(records),
        "errors": len(errors),
        "o3_jd_role_family_distribution": dict(jd_dist),
        "o3_jd_seniority_distribution": dict(sen_dist),
        "o3_email_outcome_distribution": dict(email_dist),
        "hard_seniority_mismatch_detail": sorted(
            mismatch_detail, key=lambda d: str(d.get("title"))
        ),
        "error_detail": errors[:20],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\n[generate] wrote {len(records)} records → {out}", file=sys.stderr)
    return 0


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _run_verify_build(stamp: str, jsonl: str) -> int:
    """Build the Excel verify sheet from a pre-label JSONL."""
    from job_assist.eval.verify import build_workbook

    prelabels = _read_jsonl(jsonl)
    wb = build_workbook(prelabels)
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    out = DATASETS_DIR / f"verify_sheet.{stamp}.xlsx"
    wb.save(out)
    jd = sum(1 for r in prelabels if r.get("kind") == "jd")
    em = sum(1 for r in prelabels if r.get("kind") == "email")
    print(json.dumps({"jd_rows": jd, "email_rows": em, "sheet": str(out.name)}, indent=2))
    print(f"\n[verify-build] wrote {out}", file=sys.stderr)
    return 0


def _run_verify_score(stamp: str, jsonl: str, xlsx: str) -> int:
    """Score the edited verify sheet → verified labels JSONL + override summary."""
    from openpyxl import load_workbook

    from job_assist.eval.verify import read_verify_rows, score

    prelabels = _read_jsonl(jsonl)
    wb = load_workbook(xlsx, data_only=True)
    jd_rows, email_rows = read_verify_rows(wb)
    verified, summary = score(prelabels, jd_rows, email_rows)

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    labels_out = DATASETS_DIR / f"verified_labels.{stamp}.jsonl"
    with labels_out.open("w", encoding="utf-8") as fh:
        for rec in verified:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    summary_out = DATASETS_DIR / f"override_summary.{stamp}.json"
    summary_out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(
        f"\n[verify-score] wrote {len(verified)} labels → {labels_out.name}; "
        f"summary → {summary_out.name}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.run")
    parser.add_argument("mode", choices=["count", "generate", "verify-build", "verify-score"])
    parser.add_argument(
        "--stamp",
        required=True,
        help="Timestamp tag for artifacts (passed in for reproducibility).",
    )
    parser.add_argument("--jsonl", help="Pre-label JSONL path (verify-build / verify-score).")
    parser.add_argument("--xlsx", help="Edited verify sheet path (verify-score).")
    args = parser.parse_args(argv)
    if args.mode == "count":
        return _run_count(args.stamp)
    if args.mode == "generate":
        return _run_generate(args.stamp)
    if args.mode == "verify-build":
        if not args.jsonl:
            parser.error("verify-build requires --jsonl")
        return _run_verify_build(args.stamp, args.jsonl)
    # verify-score
    if not args.jsonl or not args.xlsx:
        parser.error("verify-score requires --jsonl and --xlsx")
    return _run_verify_score(args.stamp, args.jsonl, args.xlsx)


if __name__ == "__main__":
    raise SystemExit(main())
