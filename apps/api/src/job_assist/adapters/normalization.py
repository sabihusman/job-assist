"""Shared normalization helpers reused across ATS adapters.

These are pure functions over strings / payload dicts — no ATS-specific
state. Each adapter is free to bypass them if its source carries
better-quality structured data than the regex heuristics here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# ── Title abbreviation expansion ──────────────────────────────────────────────
#
# Order matters: APM must be expanded before PM (otherwise "APM" → "Aproduct
# manager"). Sr./Jr./VP/GM are expanded with a trailing space so that
# downstream regexes (e.g. detect_seniority) see whole words.
_ABBREVS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bAPM\b"), "associate product manager"),
    (re.compile(r"\bPM\b"), "product manager"),
    (re.compile(r"\bSr\.?\s*", re.IGNORECASE), "senior "),
    (re.compile(r"\bJr\.?\s*", re.IGNORECASE), "junior "),
    (re.compile(r"\bVP\b"), "vice president"),
    (re.compile(r"\bGM\b"), "general manager"),
]


def _expand_abbrevs(title: str) -> str:
    for pattern, replacement in _ABBREVS:
        title = pattern.sub(replacement, title)
    return title


def normalize_title(raw_title: str) -> str:
    """Lowercase + expand abbreviations + collapse whitespace."""
    title = _expand_abbrevs(raw_title)
    title = title.lower()
    return re.sub(r"\s+", " ", title).strip()


# ── HTML stripping ────────────────────────────────────────────────────────────


def strip_html(html: str) -> str:
    """Strip HTML to plain text using selectolax; preserve logical line breaks.

    Falls back to a crude regex strip if selectolax raises for any reason.
    """
    if not html:
        return ""
    try:
        from selectolax.parser import HTMLParser

        parser = HTMLParser(html)
        text = parser.text(separator="\n")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ── Location parsing ──────────────────────────────────────────────────────────


def parse_location(
    location_raw: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """Return (locations_normalized, remote_type_str).

    Heuristics (intentionally lo-fi for Week 1):
      - "Remote"              → [{remote_type: remote}]          → remote
      - "City, ST"            → [{city, state, country: US, remote_type: onsite}]
      - Multiple via '/' ';'  → split and parse each part
      - Anything else         → [{city: raw, remote_type: unknown}]
    """
    if not location_raw:
        return [], "unknown"

    parts = [p.strip() for p in re.split(r"[/;]", location_raw) if p.strip()]
    results: list[dict[str, Any]] = []

    for part in parts:
        if re.search(r"\bremote\b", part, re.IGNORECASE):
            results.append({"remote_type": "remote"})
        else:
            m = re.match(r"^(.+?),\s*([A-Z]{2})$", part)
            if m:
                results.append(
                    {
                        "city": m.group(1).strip(),
                        "state": m.group(2),
                        "country": "US",
                        "remote_type": "onsite",
                    }
                )
            else:
                results.append({"city": part, "remote_type": "unknown"})

    if not results:
        return [], "unknown"

    if any(r.get("remote_type") == "remote" for r in results):
        remote_type = "remote"
    elif all(r.get("remote_type") == "onsite" for r in results):
        remote_type = "onsite"
    else:
        remote_type = "unknown"

    return results, remote_type


# ── Seniority / role family heuristics ────────────────────────────────────────


def detect_seniority(normalized_title: str) -> str:
    """Derive SeniorityLevel enum value from a normalised title string."""
    t = normalized_title
    if "intern" in t:
        return "intern"
    if "principal" in t:
        return "principal_pm"
    if "staff" in t or re.search(r"\blead\b", t):
        return "lead_pm"
    if "senior" in t:
        return "senior_pm"
    if "associate" in t or "apm" in t:
        return "apm"
    if "product manager" in t or "product owner" in t or "product management" in t:
        return "pm"
    return "unknown"


def detect_role_family(normalized_title: str) -> str:
    """Derive RoleFamily enum value from a normalised title string."""
    t = normalized_title
    if "product marketing" in t:
        return "product_marketing"
    if "product owner" in t:
        return "product_owner"
    if "program manager" in t or "program management" in t:
        return "program_management"
    if "product manager" in t or "product management" in t:
        return "product_management"
    return "other"


# ── Hashing ───────────────────────────────────────────────────────────────────


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_content_hash(
    canonical_company_name: str,
    normalized_title: str,
    locations_normalized: list[dict[str, Any]],
) -> str:
    """Stable hash over (company, title, locations) — identifies a unique role."""
    payload = json.dumps(
        {
            "company": canonical_company_name,
            "title": normalized_title,
            "locations": locations_normalized,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256(payload)
