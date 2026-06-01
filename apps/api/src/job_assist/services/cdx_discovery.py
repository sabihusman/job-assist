"""Pure helpers for Common Crawl CDX handle discovery (Slice 3b).

The network orchestration lives in ``scripts/discover_handles.py`` (not
importable / not unit-tested — it makes live HTTP calls to Common
Crawl). Everything testable — host→ats mapping, slug extraction, the
reserved-segment exclusions, CDX-line parsing, dedup — lives here so it
can be imported and pinned by ``tests/services/test_cdx_discovery.py``.

What a "slug" is: the first path segment of an ATS board URL, which is
the company's board token / handle. E.g.
``boards.greenhouse.io/stripe/jobs/123`` → ``stripe``. That handle is
exactly what ``discovered_handle.handle`` stores and what the adapters
fetch with.

License note: this module derives company slugs from URLs (facts), not
from crawled page content, so a clean-room CDX scan carries no CC BY-NC
constraint — see the PR B Read-First.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from urllib.parse import urlsplit

# ATS → the host(s) whose URLs carry that ATS's board slugs as the first
# path segment. Greenhouse serves two host variants (the older
# ``boards.`` and the newer ``job-boards.``); both put the slug first.
ATS_HOSTS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
}

# Reverse lookup: host → ats. Built once at import.
_HOST_TO_ATS: dict[str, str] = {host: ats for ats, hosts in ATS_HOSTS.items() for host in hosts}

# First-path segments that are NOT company slugs — ATS chrome, embeds,
# static assets, API roots. A capture whose first segment is one of
# these is dropped.
_RESERVED_SEGMENTS: frozenset[str] = frozenset(
    {
        "",  # bare host root, no slug
        "embed",
        "embed_job_app",
        "api",
        "static",
        "assets",
        "favicon.ico",
        "robots.txt",
        "sitemap.xml",
        "_next",
        "images",
        "img",
        "css",
        "js",
    }
)

# A valid board slug: lowercase alnum, internal hyphens allowed, must
# start alnum. Drops junk like ``%20foo``, ``foo.css``, uppercase noise.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# CDX ``status`` values we accept. 200 = live capture; 30x = a redirect
# whose source URL still names a real slug (company kept the board, URL
# shape shifted). 404/410/5xx are dropped — the slug was dead even at
# crawl time.
_ACCEPTED_STATUSES: frozenset[str] = frozenset({"200", "301", "302", "307", "308"})


def host_to_ats(host: str) -> str | None:
    """Return the ATS for a CDX-captured host, or None if not one we scan.

    Tolerates a leading ``www.`` and a trailing port, lowercases.
    """
    h = host.lower().strip()
    if h.startswith("www."):
        h = h[4:]
    h = h.split(":", 1)[0]
    return _HOST_TO_ATS.get(h)


def extract_slug(url: str, *, expected_ats: str | None = None) -> tuple[str, str] | None:
    """Extract ``(ats, slug)`` from one ATS board URL, or None.

    Returns None when:
      * the URL doesn't parse or names a host we don't scan,
      * the first path segment is reserved / empty,
      * the slug fails the ``_SLUG_RE`` shape check,
      * ``expected_ats`` is given and the URL's host belongs to a
        different ATS (lets the caller pin one host's results).
    """
    try:
        parts = urlsplit(url if "://" in url else f"http://{url}")
    except ValueError:
        return None
    ats = host_to_ats(parts.netloc)
    if ats is None:
        return None
    if expected_ats is not None and ats != expected_ats:
        return None
    # First non-empty path segment.
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None
    slug = segments[0].lower()
    if slug in _RESERVED_SEGMENTS:
        return None
    if not _SLUG_RE.match(slug):
        return None
    return (ats, slug)


def parse_cdx_jsonl(text: str) -> Iterator[dict[str, str]]:
    """Yield CDX records from a JSONL response body.

    Blank lines and unparseable lines are skipped (the CDX server
    occasionally emits a stray non-JSON line on error). Each record is a
    dict with at least ``url``; ``status`` present when ``fl`` included
    it.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "url" in obj:
            yield obj


def slugs_from_cdx_records(
    records: Iterator[dict[str, str]] | list[dict[str, str]],
    *,
    expected_ats: str,
) -> set[str]:
    """Reduce CDX records for one host to a deduped set of valid slugs.

    Applies the status filter (accept 200/30x, drop 404/5xx) and the
    slug extraction + reserved/shape filters. Returns the distinct slug
    set — re-pulls and the many captures per company collapse here.
    """
    out: set[str] = set()
    for rec in records:
        status = str(rec.get("status", "200"))
        if status not in _ACCEPTED_STATUSES:
            continue
        extracted = extract_slug(rec["url"], expected_ats=expected_ats)
        if extracted is None:
            continue
        out.add(extracted[1])
    return out


def dedup_against_existing(
    candidates: dict[str, set[str]],
    existing: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Flatten ``{ats: {slug, ...}}`` into a sorted ``(ats, slug)`` list,
    dropping pairs already present in ``existing``.

    ``existing`` is the set of ``(ats, handle)`` already in
    ``discovered_handle`` (curated handles need not be excluded — the
    seed step skips any duplicate by name anyway, and a broad shell for
    a curated handle is harmless). Sorted output makes the reviewable
    file stable across runs.
    """
    new: list[tuple[str, str]] = []
    for ats in sorted(candidates):
        for slug in sorted(candidates[ats]):
            if (ats, slug) not in existing:
                new.append((ats, slug))
    return new


__all__ = [
    "ATS_HOSTS",
    "dedup_against_existing",
    "extract_slug",
    "host_to_ats",
    "parse_cdx_jsonl",
    "slugs_from_cdx_records",
]
