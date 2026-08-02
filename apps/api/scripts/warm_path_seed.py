"""Warm-path Piece 2 ops: seed the approved 15-company cohort, reactivate
Athene, verify the plan, and (with --sweep) run the ONE approved test sweep.

Usage (from apps/api, AFTER the warm-path-ingest PR has deployed):

    # PowerShell
    $env:API_URL = "https://api-production-ca5ad.up.railway.app"
    $env:API_AUTH_TOKEN = "<token>"
    uv run --no-sync python scripts/warm_path_seed.py            # seed + verify
    uv run --no-sync python scripts/warm_path_seed.py --sweep    # + test sweep

Safety:
  * PREFLIGHT: aborts unless GET /admin/ingest/fantastic-plan exists (the old
    deployment's seed endpoint silently DROPS the `source` field — rows would
    be born 'curated' and ride the DAILY paid sweep).
  * Seeding is idempotent (the endpoint skips existing names).
  * Athene is REACTIVATED via crawl-config using its exact stored name
    (matched by normalized name from /companies), never re-seeded.
"""

from __future__ import annotations

import os
import sys

import httpx

sys.path.insert(0, "src")
from job_assist.services.company_name_match import normalize_company_name

API_URL = os.environ.get("API_URL", "").rstrip("/")
TOKEN = os.environ.get("API_AUTH_TOKEN", "")
if not API_URL or not TOKEN:
    sys.exit("Set API_URL and API_AUTH_TOKEN env vars.")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# The approved seed list (user-locked). ats=workday routes them into the
# fantastic (Apify) path; the actor targets by DOMAIN, so ats is routing only.
SEED = [
    ("John Deere", "deere.com"),
    ("Collins Aerospace", "collinsaerospace.com"),
    ("Parker Hannifin", "parker.com"),
    ("Archer Daniels Midland", "adm.com"),
    ("Schneider Electric", "se.com"),
    ("Smithbucklin", "smithbucklin.com"),
    ("Hearst", "hearst.com"),
    ("Centene", "centene.com"),
    ("AbbVie", "abbvie.com"),
    ("Advocate Health", "advocatehealth.org"),
    ("Mayo Clinic", "mayoclinic.org"),
    ("Optum", "optum.com"),
    ("Integrated DNA Technologies", "idtdna.com"),
    ("Stryker", "stryker.com"),
    ("MidAmerican Energy", "midamericanenergy.com"),
]


def main() -> None:
    do_sweep = "--sweep" in sys.argv
    with httpx.Client(timeout=60) as client:
        # ── Preflight: the new endpoints must exist ──────────────────────
        r = client.get(
            f"{API_URL}/admin/ingest/fantastic-plan",
            params={"source": "warm_path"},
            headers=HEADERS,
        )
        if r.status_code == 404:
            sys.exit(
                "ABORT: /admin/ingest/fantastic-plan not found — the warm-path PR "
                "has not deployed yet. Seeding now would silently drop source= "
                "and the rows would ride the DAILY sweep. Wait for Railway."
            )
        r.raise_for_status()
        print(f"preflight ok — current warm-path cohort: {r.json()['count']}")

        # ── Seed the 15 (idempotent; born warm_path) ─────────────────────
        rows = [
            {
                "name": name,
                "tier": None,
                "ats": "workday",
                "domain": domain,
                "source": "warm_path",
                "notes": "warm-path seed (alumni cohort, 2026-06-11)",
            }
            for name, domain in SEED
        ]
        r = client.post(f"{API_URL}/admin/seed/target-companies", json=rows, headers=HEADERS)
        r.raise_for_status()
        print(f"seed: {r.json()}")

        # ── Reactivate Athene (exact stored name via normalized match) ───
        companies: list[dict] = []
        offset = 0
        while True:
            r = client.get(
                f"{API_URL}/companies",
                params={"limit": 50, "offset": offset},
                headers=HEADERS,
            )
            r.raise_for_status()
            body = r.json()
            companies.extend(body["items"])
            offset += len(body["items"])
            if offset >= body["total"] or not body["items"]:
                break
        athene = [c for c in companies if normalize_company_name(c["name"]) == "athene"]
        if not athene:
            print("WARN: no Athene row found in target_company — nothing reactivated")
        else:
            stored_name = athene[0]["name"]
            r = client.post(
                f"{API_URL}/admin/companies/crawl-config",
                json=[{"name": stored_name, "source": "warm_path"}],
                headers=HEADERS,
            )
            r.raise_for_status()
            print(f"athene reactivated as warm_path (stored name: {stored_name!r}): {r.json()}")

        # ── Verify the cohort plan (read-only, no Apify spend) ───────────
        r = client.get(
            f"{API_URL}/admin/ingest/fantastic-plan",
            params={"source": "warm_path"},
            headers=HEADERS,
        )
        r.raise_for_status()
        plan = r.json()
        print(f"\nwarm-path plan: {plan['count']} companies")
        for c in plan["companies"]:
            print(f"  {c['name']:<30} {c['apify_domain']:<28} last_swept={c['last_swept_at']}")

        # ── ONE test sweep (only with --sweep) ───────────────────────────
        if do_sweep:
            print("\nrunning the test sweep (Apify spend: ~pennies)…")
            r = client.post(
                f"{API_URL}/admin/ingest/fantastic",
                params={"source": "warm_path"},
                headers=HEADERS,
                timeout=600,
            )
            r.raise_for_status()
            sweep = r.json()
            print(f"\nsweep complete — {sweep['employers']} employers:")
            print(f"  {'company':<30} {'status':<10} {'fetched':>7} {'new':>5} {'updated':>7}")
            for res in sweep["results"]:
                print(
                    f"  {res['company']:<30} {res['status']:<10} "
                    f"{res['postings_fetched']:>7} {res['postings_new']:>5} "
                    f"{res['postings_updated']:>7}"
                )
        else:
            print("\n(no sweep run — re-run with --sweep for the one-off test sweep,")
            print(" or trigger the 'Warm-path ingest' workflow from the Actions tab)")


if __name__ == "__main__":
    main()
