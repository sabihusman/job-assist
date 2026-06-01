"""Discover ATS board handles (Slice 2 trial seed + Slice 3b CDX scan).

Two modes:

  --mode cdx   (default)  Clean-room Common Crawl CDX scan. Enumerates
                          board URLs under boards.greenhouse.io /
                          job-boards.greenhouse.io / jobs.lever.co /
                          jobs.ashbyhq.com, extracts the company slug
                          (first path segment), dedups, optionally
                          validates each against the live ATS API, and
                          writes a REVIEWABLE JSON FILE. Does NOT seed
                          prod — the operator inspects the file, then
                          seeds via
                          ``POST /admin/discovered-handles/seed -d @file``.

  --mode trial-seed       Seeds the ~50 hand-curated TRIAL_HANDLES into
                          the configured DATABASE_URL (Slice 2 path,
                          preserved).

The pure slug/parse/dedup logic lives in
``job_assist.services.cdx_discovery`` (unit-tested); this script is the
network + file-IO orchestration on top.

CDX usage (see PR B Read-First):
  * Index list read live from collinfo.json — never hardcoded.
  * matchType=host, output=json (JSONL), fl=url,status.
  * Paginated via showNumPages → page=0..N-1.
  * Serial, polite (~1 req/s), 503-tolerant. Querying several monthly
    indexes unions their slug sets for coverage.

Run locally (network required — NOT in CI):
    cd apps/api
    uv run python scripts/discover_handles.py --indexes 2 --validate
    # review apps/api/data/discovered_handles_<date>.json, then:
    curl -X POST -H 'Content-Type: application/json' \
         -d @data/discovered_handles_<date>.json \
         https://<host>/admin/discovered-handles/seed

License: clean-room CDX scan — derives slugs (facts) from URLs, not
crawled page content; no CC BY-NC constraint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from job_assist.config import settings
from job_assist.services.broad_ingest import seed_discovered_handles
from job_assist.services.cdx_discovery import (
    ATS_HOSTS,
    dedup_against_existing,
    parse_cdx_jsonl,
    slugs_from_cdx_records,
)

_COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
_DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data"

# ── Slice 2 trial set (hand-curated, preserved) ────────────────────────────
TRIAL_HANDLES: tuple[tuple[str, str], ...] = (
    ("greenhouse", "stripe"),
    ("ashby", "moderntreasury"),
    ("lever", "alpaca"),
    # (full 50-handle list lived here in Slice 2; trimmed in the CDX
    # era — the operator seeds the CDX file instead. Kept as a tiny
    # smoke set for ``--mode trial-seed``.)
)


# ── CDX network orchestration ──────────────────────────────────────────────


async def _fetch_recent_index_apis(client: httpx.AsyncClient, n: int) -> list[str]:
    """Return the ``cdx-api`` URLs of the ``n`` most-recent monthly indexes."""
    resp = await client.get(_COLLINFO_URL, timeout=30.0)
    resp.raise_for_status()
    info: list[dict[str, Any]] = resp.json()
    # collinfo.json is newest-first; ``cdx-api`` is the full ...-index URL.
    return [entry["cdx-api"] for entry in info[:n] if "cdx-api" in entry]


async def _fetch_host_slugs(
    client: httpx.AsyncClient, index_api: str, host: str, ats: str, *, max_pages: int
) -> set[str]:
    """Paginate one host on one index; return its deduped slug set."""
    base = {"url": host, "matchType": "host", "output": "json", "fl": "url,status"}
    # Page count first.
    try:
        meta_resp = await client.get(
            index_api, params={**base, "showNumPages": "true"}, timeout=60.0
        )
        meta_resp.raise_for_status()
        pages = int(meta_resp.json().get("pages", 0))
    except (httpx.HTTPError, ValueError, KeyError):
        return set()

    slugs: set[str] = set()
    for page in range(min(pages, max_pages)):
        for attempt in range(4):
            try:
                resp = await client.get(
                    index_api, params={**base, "page": str(page)}, timeout=120.0
                )
                if resp.status_code == 503:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                slugs |= slugs_from_cdx_records(parse_cdx_jsonl(resp.text), expected_ats=ats)
                break
            except httpx.HTTPError:
                await asyncio.sleep(2 * (attempt + 1))
        # Politeness gap between pages.
        await asyncio.sleep(1.0)
    return slugs


_VALIDATE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


async def _validate_slug(client: httpx.AsyncClient, ats: str, slug: str) -> bool:
    """True iff the slug resolves to a live, non-empty board."""
    url = _VALIDATE_URLS[ats].format(slug=slug)
    try:
        resp = await client.get(url, timeout=30.0)
    except httpx.HTTPError:
        return False
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    if ats == "lever":
        return isinstance(data, list) and len(data) > 0
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return isinstance(jobs, list) and len(jobs) > 0


async def _load_existing(ats_filter: set[str]) -> set[tuple[str, str]]:
    """Read existing ``(ats, handle)`` pairs from the DB for dedup.

    Best-effort: returns empty set if DATABASE_URL is unset/unreachable
    (the seed step dedups again at insert time anyway).
    """
    if not settings.database_url:
        return set()
    from job_assist.db.models import DiscoveredHandle

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, class_=AsyncSession)
    try:
        async with factory() as session:
            rows = (
                await session.execute(select(DiscoveredHandle.ats, DiscoveredHandle.handle))
            ).all()
    except Exception:
        return set()
    finally:
        await engine.dispose()
    return {(a, h) for a, h in rows if a in ats_filter}


async def _run_cdx_scan(args: argparse.Namespace) -> None:
    ats_filter = set(args.ats.split(",")) if args.ats else set(ATS_HOSTS)
    out_path = (
        Path(args.out) if args.out else _DEFAULT_OUT_DIR / f"discovered_handles_{args.tag}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        index_apis = await _fetch_recent_index_apis(client, args.indexes)
        print(f"Scanning {len(index_apis)} CDX index(es) for {sorted(ats_filter)}")

        candidates: dict[str, set[str]] = {ats: set() for ats in ats_filter}
        for index_api in index_apis:
            for ats in ats_filter:
                for host in ATS_HOSTS[ats]:
                    found = await _fetch_host_slugs(
                        client, index_api, host, ats, max_pages=args.max_pages
                    )
                    candidates[ats] |= found
                    print(f"  {index_api.rsplit('/', 1)[-1]}  {host}: {len(found)} slugs")

        existing = await _load_existing(ats_filter)
        new_pairs = dedup_against_existing(candidates, existing)
        print(
            f"Candidates: {sum(len(s) for s in candidates.values())} distinct; "
            f"{len(new_pairs)} new after dedup vs {len(existing)} existing"
        )

        if args.validate:
            print("Validating against live ATS APIs (this is the slow part)...")
            validated: list[tuple[str, str]] = []
            for ats, slug in new_pairs:
                if await _validate_slug(client, ats, slug):
                    validated.append((ats, slug))
                await asyncio.sleep(0.25)
            print(f"Validated live: {len(validated)} / {len(new_pairs)}")
            new_pairs = validated

    payload = [{"ats": ats, "handle": handle} for ats, handle in new_pairs]
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {len(payload)} handles to {out_path}")
    print("Review the file, then seed:")
    print(
        f"  curl -X POST -H 'Content-Type: application/json' "
        f"-d @{out_path} https://<host>/admin/discovered-handles/seed"
    )


async def _run_trial_seed() -> None:
    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as session:
            inserted, skipped = await seed_discovered_handles(session, list(TRIAL_HANDLES))
    finally:
        await engine.dispose()
    print(f"Trial seed: {inserted} inserted, {skipped} already existed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover ATS board handles.")
    parser.add_argument("--mode", choices=["cdx", "trial-seed"], default="cdx")
    parser.add_argument(
        "--indexes", type=int, default=1, help="how many recent monthly CDX indexes to union"
    )
    parser.add_argument("--ats", default="", help="comma list (greenhouse,lever,ashby); blank=all")
    parser.add_argument(
        "--validate", action="store_true", help="probe each slug against the live ATS API"
    )
    parser.add_argument(
        "--out", default="", help="output JSON path (default data/discovered_handles_<tag>.json)"
    )
    parser.add_argument("--tag", default="latest", help="filename tag for the default output path")
    parser.add_argument(
        "--max-pages", type=int, default=1000, help="safety cap on CDX pages per host"
    )
    args = parser.parse_args()

    if args.mode == "trial-seed":
        asyncio.run(_run_trial_seed())
    else:
        asyncio.run(_run_cdx_scan(args))


if __name__ == "__main__":
    main()
