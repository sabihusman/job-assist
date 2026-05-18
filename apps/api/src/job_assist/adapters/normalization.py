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


def normalize_org_field(raw: str | None) -> str | None:
    """Cleanup for department / team strings before they hit ``job_posting``.

    Rules (PR #28a):
      * strip surrounding whitespace
      * collapse empty-after-strip to ``None``
      * truncate to 200 chars (defensive; real ATS data is rarely > 50)
      * preserve case — these are authored values, not classifications

    ``None`` passes through untouched.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if len(cleaned) > 200:
        cleaned = cleaned[:200]
    return cleaned


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


# ── Compensation parsing ──────────────────────────────────────────────────────
#
# Free-form compensation summary strings appear on Ashby (and will appear on
# Workday + JSearch later). This helper extracts (min, max, currency, period)
# from the common formats; it never raises and returns all-None when nothing
# parseable is found, so adapters can safely chain it.
#
# Supported formats (case-insensitive):
#   "$150K"                     → (150000, 150000, "USD", "annual")
#   "$140K - $180K"             → (140000, 180000, "USD", "annual")
#   "$140,000 - $180,000"       -> (140000, 180000, "USD", "annual")
#   "$50/hr - $75/hr"           -> (50,     75,     "USD", "hourly")
#   "£100K"                     → (100000, 100000, "GBP", "annual")
#   "€100K"                     → (100000, 100000, "EUR", "annual")
#   "C$120K - C$150K"           -> (120000, 150000, "CAD", "annual")

# Match a number anchored to one of the supported currency glyphs, so that
# stray digits elsewhere in the summary (e.g. "Q3 2026") don't get picked up.
_COMP_NUMBER_RE = re.compile(r"[$£€]\s*(\d[\d,]*(?:\.\d+)?)\s*(K)?", re.IGNORECASE)
_COMP_HOURLY_RE = re.compile(r"/\s*(hr|hour)\b", re.IGNORECASE)


def parse_compensation(
    summary: str | None,
) -> tuple[int | None, int | None, str | None, str | None]:
    """Parse a compensation-summary string into structured fields.

    Returns ``(salary_min, salary_max, currency, period)``. Any input the
    parser doesn't understand returns all four ``None`` — never raises.
    """
    if not summary or not summary.strip():
        return None, None, None, None

    s = summary.strip()

    # Currency: check C$ before plain $ so Canadian dollars aren't shadowed
    # by USD. £ / € are unambiguous.
    currency: str | None
    if "C$" in s:
        currency = "CAD"
    elif "$" in s:
        currency = "USD"
    elif "£" in s:
        currency = "GBP"
    elif "€" in s:
        currency = "EUR"
    else:
        currency = None

    # Period: only meaningful if we found a currency anchor.
    period: str | None = None
    if currency is not None:
        period = "hourly" if _COMP_HOURLY_RE.search(s) else "annual"

    # Extract every currency-anchored number in the string.
    nums: list[int] = []
    for raw_num, k_suffix in _COMP_NUMBER_RE.findall(s):
        cleaned = raw_num.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if k_suffix:
            value *= 1000
        nums.append(int(value))

    if not nums:
        return None, None, None, None

    if len(nums) == 1:
        salary_min = salary_max = nums[0]
    else:
        salary_min, salary_max = nums[0], nums[1]

    return salary_min, salary_max, currency, period
