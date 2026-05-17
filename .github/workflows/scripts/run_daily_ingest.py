#!/usr/bin/env python3
"""Drive the daily ingest cron from .github/workflows/ingest-daily.yml.

Reads the ingest plan from stdin (a JSON array of {ats, handle} objects),
then POSTs to ``$API_URL/admin/ingest/{ats}/{handle}`` for each entry,
sequentially, with a polite ``THROTTLE_SECONDS`` gap between calls.

Exits non-zero on any failure so the workflow surfaces a red check and
GitHub fires the standard "workflow failed" email to repo admins.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# Polite gap between ATS hits — avoids rate-limit headaches on
# Greenhouse / Lever / Ashby and keeps Railway's single replica from
# contention.
THROTTLE_SECONDS = float(os.environ.get("THROTTLE_SECONDS", "5"))
# Per-call ceiling — Greenhouse + Lever respond in 1-3s; Ashby +
# bigger boards (Anthropic at 411) take longer. 120s leaves margin.
INGEST_TIMEOUT_S = int(os.environ.get("INGEST_TIMEOUT_S", "120"))


def main() -> int:
    api_url = os.environ.get("API_URL", "").rstrip("/")
    if not api_url:
        print("FATAL: API_URL env var is unset", file=sys.stderr)
        return 2

    try:
        plan: list[dict[str, str]] = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"FATAL: ingest plan was not valid JSON: {exc}", file=sys.stderr)
        return 2

    total = len(plan)
    if total == 0:
        print("Ingest plan is empty — nothing to do.")
        return 0

    print(f"Running {total} ingestions sequentially "
          f"(throttle={THROTTLE_SECONDS}s, timeout={INGEST_TIMEOUT_S}s/call).")

    failures: list[tuple[str, str, str]] = []
    for i, item in enumerate(plan, 1):
        ats = item["ats"]
        handle = item["handle"]
        url = f"{api_url}/admin/ingest/{ats}/{handle}"
        print(f"[{i}/{total}] POST {ats}/{handle} …", flush=True)

        try:
            result = subprocess.run(
                [
                    "curl",
                    "-fsS",
                    "--max-time",
                    str(INGEST_TIMEOUT_S),
                    "-X",
                    "POST",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=INGEST_TIMEOUT_S + 10,
            )
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT after {INGEST_TIMEOUT_S}s", flush=True)
            failures.append((ats, handle, f"timeout after {INGEST_TIMEOUT_S}s"))
        else:
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "no output").strip()[:300]
                print(f"  FAILED (curl rc={result.returncode}): {err}", flush=True)
                failures.append((ats, handle, err))
            else:
                try:
                    data = json.loads(result.stdout)
                    fetched = data.get("postings_fetched", "?")
                    new = data.get("postings_new", "?")
                    updated = data.get("postings_updated", "?")
                    status = data.get("status", "?")
                    print(
                        f"  OK status={status} fetched={fetched} "
                        f"new={new} updated={updated}",
                        flush=True,
                    )
                except json.JSONDecodeError:
                    # 200 but body wasn't JSON — surface anyway, don't fail.
                    print(f"  OK (non-JSON body: {result.stdout[:120]!r})", flush=True)

        if i < total:
            time.sleep(THROTTLE_SECONDS)

    print()
    if failures:
        print(f"{len(failures)}/{total} ingestions failed:")
        for ats, handle, err in failures:
            print(f"  - {ats}/{handle}: {err[:200]}")
        return 1

    print(f"All {total} ingestions completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
