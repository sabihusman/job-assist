"""Piece 3 GATE 1: validate the strategy_ops classifier bucket on real titles.

Calls the LIVE Gemini classifier (the updated v5 prompt in
services/classifier.py) over a labeled title set, TITLE-ONLY (jd_text="") —
the conservative case, since production classification also sees the JD.

The binary line that matters (per the gate): strategy titles → strategy_ops;
generic operations / delivery titles → anything BUT strategy_ops. Control
titles guard against regressions in the existing families.

Usage (from apps/api — needs only the Gemini key, no API/DB):

    $env:GEMINI_API_KEY = "<key>"
    uv run --no-sync python scripts/validate_strategy_classifier.py

~35 calls with 4s pacing (free-tier 15 RPM) ≈ 2.5 minutes.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "src")

from job_assist.services.classifier import classify_posting

if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("Set GEMINI_API_KEY (the script calls Gemini directly; no API/DB needed).")

# (title, expectation) — expectation is either the exact family, or one of the
# special markers:
#   "NOT_STRATEGY"  — any family except strategy_ops is a pass (the gate's
#                     keep/drop boundary; whether ops lands program_management
#                     or other is secondary and reported, not judged)
#   "STRATEGY"      — must be strategy_ops
CASES: list[tuple[str, str]] = [
    # ── strategy family (must classify strategy_ops) ─────────────────────
    ("Strategy & Operations Manager", "STRATEGY"),
    ("Senior Strategy & Operations Manager", "STRATEGY"),
    ("Manager, Corporate Strategy", "STRATEGY"),
    ("Director, Business Strategy", "STRATEGY"),
    ("Business Operations Manager", "STRATEGY"),
    ("BizOps & Strategy Associate", "STRATEGY"),
    ("Chief of Staff", "STRATEGY"),
    ("Chief of Staff to the CFO", "STRATEGY"),
    ("Strategy Consultant", "STRATEGY"),
    ("Senior Manager, Enterprise Strategy", "STRATEGY"),
    ("Strategy and Planning Manager", "STRATEGY"),
    ("Corporate Strategy & Development Analyst", "STRATEGY"),
    # ── generic ops / delivery (must NOT classify strategy_ops) ──────────
    ("Operations Manager", "NOT_STRATEGY"),
    ("Plant Operations Manager", "NOT_STRATEGY"),
    ("Warehouse Operations Supervisor", "NOT_STRATEGY"),
    ("IT Project Manager", "NOT_STRATEGY"),
    ("Clinical Operations Coordinator", "NOT_STRATEGY"),
    ("Network Operations Engineer", "NOT_STRATEGY"),
    ("Sales Operations Analyst", "NOT_STRATEGY"),
    ("Manufacturing Operations Lead", "NOT_STRATEGY"),
    ("Security Operations Analyst", "NOT_STRATEGY"),
    ("Facilities Project Manager", "NOT_STRATEGY"),
    ("Revenue Operations Specialist", "NOT_STRATEGY"),
    ("Field Operations Manager", "NOT_STRATEGY"),
    ("Director of Plant Operations", "NOT_STRATEGY"),
    ("Supply Chain Operations Analyst", "NOT_STRATEGY"),
    # ── controls (existing families must not regress) ────────────────────
    ("Senior Product Manager", "product_management"),
    ("Product Manager, Payments Platform", "product_management"),
    ("Product Owner", "product_owner"),
    ("Technical Program Manager", "program_management"),
    ("Product Marketing Manager", "product_marketing"),
    ("Software Engineer", "other"),
    ("Product Operations Specialist", "program_management"),
    ("Senior Product Designer", "other"),
    ("Customer Success Manager", "other"),
]


async def main() -> None:
    rows: list[tuple[str, str, str, bool]] = []
    for i, (title, expected) in enumerate(CASES):
        family, _seniority = await classify_posting("", title)
        if expected == "STRATEGY":
            ok = family == "strategy_ops"
        elif expected == "NOT_STRATEGY":
            ok = family != "strategy_ops"
        else:
            ok = family == expected
        rows.append((title, expected, family, ok))
        mark = "ok " if ok else "MISS"
        print(f"[{i + 1:>2}/{len(CASES)}] {mark} {title:<42} -> {family}")
        await asyncio.sleep(4)  # free-tier RPM pacing

    strat = [r for r in rows if r[1] == "STRATEGY"]
    notstrat = [r for r in rows if r[1] == "NOT_STRATEGY"]
    ctrl = [r for r in rows if r[1] not in ("STRATEGY", "NOT_STRATEGY")]
    print("\n── summary ──────────────────────────────────────────")
    print(f"strategy titles → strategy_ops : {sum(r[3] for r in strat)}/{len(strat)}")
    print(f"generic ops NOT strategy_ops   : {sum(r[3] for r in notstrat)}/{len(notstrat)}")
    print(f"controls unchanged             : {sum(r[3] for r in ctrl)}/{len(ctrl)}")
    misses = [r for r in rows if not r[3]]
    if misses:
        print("\nmisses:")
        for title, expected, family, _ in misses:
            print(f"  {title:<42} expected {expected:<18} got {family}")


if __name__ == "__main__":
    asyncio.run(main())
