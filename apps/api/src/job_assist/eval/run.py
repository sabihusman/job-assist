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
import json
import sys
from pathlib import Path

from job_assist.eval.api_client import fetch_open_postings, fetch_outcome_type_breakdown
from job_assist.eval.sample import compute_counts

DATASETS_DIR = Path(__file__).parent / "datasets"


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
    """Pre-label the confirmed sample. Wired after the Phase-1 count review."""
    raise SystemExit(
        "generate mode is gated until the stratified sample is confirmed "
        "(Phase 1 review) and the JD-text / email-body fetch is wired. Run "
        "`count` first and confirm the sample sizes."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.run")
    parser.add_argument("mode", choices=["count", "generate"])
    parser.add_argument(
        "--stamp",
        required=True,
        help="Timestamp tag for artifacts (passed in for reproducibility).",
    )
    args = parser.parse_args(argv)
    if args.mode == "count":
        return _run_count(args.stamp)
    return _run_generate(args.stamp)


if __name__ == "__main__":
    raise SystemExit(main())
