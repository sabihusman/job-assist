"""Warm-path Step 1 analysis (READ-ONLY — GETs only, no seeding, no writes).

Pulls all non-archived contacts and all target_company rows through the API,
normalizes employers with the REAL ``normalize_company_name`` (the same module
the #183 badges use, so these numbers match what the feature would compute),
and reports:

  (a) unique employer count (and how many contacts had no employer at all)
  (b) overlap with the crawled target_company set
  (c) contact-count distribution (3+ vs 2 vs 1 contacts per company)
  (d) first-pass on-domain vs off-domain eyeball (keyword heuristic over names)

Usage (from apps/api):

    # PowerShell
    $env:API_URL = "https://api-production-ca5ad.up.railway.app"
    $env:API_AUTH_TOKEN = "<token>"
    uv run --no-sync python scripts/warm_path_analysis.py

    # bash
    API_URL=https://api-production-ca5ad.up.railway.app \
    API_AUTH_TOKEN=<token> uv run --no-sync python scripts/warm_path_analysis.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

import httpx

sys.path.insert(0, "src")
from job_assist.services.company_name_match import normalize_company_name

API_URL = os.environ.get("API_URL", "").rstrip("/")
TOKEN = os.environ.get("API_AUTH_TOKEN", "")
if not API_URL or not TOKEN:
    sys.exit("Set API_URL and API_AUTH_TOKEN env vars (read-only GETs only).")

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# First-pass on-domain heuristic: the operator's target domain is
# fintech / wealthtech / regtech / payments / insurance / AI. Crude name-keyword
# eyeball — companies it can't tell stay "unknown" and are listed for manual
# review rather than guessed.
_ON_DOMAIN_TOKENS = (
    "bank",
    "capital",
    "financial",
    "finance",
    "fintech",
    "invest",
    "wealth",
    "asset",
    "insurance",
    "insur",
    "mutual",
    "payments",
    "pay",
    "credit",
    "lending",
    "loan",
    "mortgage",
    "trading",
    "securities",
    "brokerage",
    "annuit",
    "actuar",
    "retirement",
    "fidelity",
    "vanguard",
    "principal",
    "transamerica",
    "athene",
    "aig",
    "allianz",
    "prudential",
    "metlife",
    "mastercard",
    "visa",
    "stripe",
    "plaid",
    "square",
    "ai",
)


def _page(client: httpx.Client, path: str, limit: int) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        r = client.get(
            f"{API_URL}{path}",
            params={"limit": limit, "offset": offset},
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        batch = body.get("items", [])
        items.extend(batch)
        offset += len(batch)
        if offset >= int(body.get("total", 0)) or not batch:
            return items


def _looks_on_domain(name: str) -> bool:
    lowered = f" {name.lower()} "
    return any(
        f" {tok}" in lowered or f"{tok} " in lowered or tok in lowered.split()
        for tok in _ON_DOMAIN_TOKENS
    )


def main() -> None:
    with httpx.Client() as client:
        contacts = _page(client, "/contacts", limit=100)
        companies = _page(client, "/companies", limit=50)

    print(f"contacts (non-archived): {len(contacts)}")
    print(f"target_company rows (crawled set): {len(companies)}")

    # (a) unique employers
    no_employer = 0
    by_key: dict[str, list[dict]] = defaultdict(list)
    display: dict[str, Counter] = defaultdict(Counter)
    for c in contacts:
        raw = (c.get("current_employer") or "").strip()
        key = normalize_company_name(raw) if raw else None
        if not key:
            no_employer += 1
            continue
        by_key[key].append(c)
        display[key][raw] += 1

    print(f"\n(a) contacts with no usable employer: {no_employer}")
    print(f"(a) UNIQUE EMPLOYERS (normalized): {len(by_key)}")

    # (b) overlap with crawled set
    crawled_keys = {
        k for k in (normalize_company_name(co.get("name") or "") for co in companies) if k
    }
    overlap = sorted(k for k in by_key if k in crawled_keys)
    print(
        f"\n(b) OVERLAP with crawled set: {len(overlap)} of {len(by_key)} "
        f"({100 * len(overlap) / max(1, len(by_key)):.0f}%)"
    )
    for k in overlap:
        print(f"      ~ {display[k].most_common(1)[0][0]} ({len(by_key[k])} contact(s))")

    # (c) distribution
    sizes = Counter(len(v) for v in by_key.values())
    three_plus = sum(n for size, n in sizes.items() if size >= 3)
    print(
        f"\n(c) DISTRIBUTION: 3+ contacts: {three_plus} companies | "
        f"2 contacts: {sizes.get(2, 0)} | 1 contact: {sizes.get(1, 0)}"
    )
    strong = sorted(by_key.items(), key=lambda kv: -len(kv[1]))
    print("    strongest warm paths (ALL companies with 2+ contacts):")
    for k, v in strong:
        if len(v) < 2:
            break
        print(f"      {len(v):>2}  {display[k].most_common(1)[0][0]}")

    # (d) on-domain eyeball
    on = [k for k in by_key if _looks_on_domain(display[k].most_common(1)[0][0])]
    print(
        f"\n(d) ON-DOMAIN eyeball (name-keyword heuristic): {len(on)} of {len(by_key)} "
        f"look fintech/financial; {len(by_key) - len(on)} look off-domain/unknown"
    )
    print("    on-domain sample:")
    for k in sorted(on)[:15]:
        print(f"      ~ {display[k].most_common(1)[0][0]}")


if __name__ == "__main__":
    main()
