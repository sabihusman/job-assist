"""Shared normalization helpers reused across ATS adapters.

These are pure functions over strings / payload dicts - no ATS-specific
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
      * preserve case - these are authored values, not classifications

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
    """Stable hash over (company, title, locations) - identifies a unique role."""
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

_COMP_HOURLY_RE = re.compile(r"/\s*(hr|hour)\b", re.IGNORECASE)

# Range-aware matcher (PR salary-parser-precision). Captures a currency-
# anchored FLOOR and an OPTIONAL ceiling after a dash / "to". The ceiling's
# own currency glyph is optional because real JDs write "$189,000-236,200"
# (only the floor is $-anchored). An optional ``/hr`` | ``/year`` unit is
# consumed between the number and the separator so "$50/hr - $75/hr" parses
# as one range, not two singles. A leading ``C`` (C$) marks Canadian dollars.
# The separator class includes the literal hyphen plus en-dash (-) and
# em-dash (-) via escapes, so the source file stays ASCII (ruff RUF001).
_COMP_RANGE_RE = re.compile(
    r"(?P<lead>C)?(?P<glyph>[$£€])\s*"
    r"(?P<floor>\d[\d,]*(?:\.\d+)?)\s*(?P<fsuf>[KM])?"
    r"(?:\s*/\s*(?:hr|hour|yr|year))?"
    r"(?:"
    r"\s*(?:[-\u2013\u2014]|to)\s*"  # hyphen, en-dash, em-dash, or 'to'
    r"C?[$£€]?\s*(?P<ceil>\d[\d,]*(?:\.\d+)?)\s*(?P<csuf>[KM])?"
    r"(?:\s*/\s*(?:hr|hour|yr|year))?"
    r")?",
    re.IGNORECASE,
)

# Magnitude sanity bounds. A garbled source ($142,400,000) or a stray small
# dollar mention ("$10 fee") shouldn't masquerade as a salary. Annual base
# pay for the roles we track sits well inside [$10k, $1M]; hourly inside
# [$10, $1k].
_ANNUAL_MIN, _ANNUAL_MAX = 10_000, 1_000_000
_HOURLY_MIN, _HOURLY_MAX = 10, 1_000
# A real salary band's ends are close; a >6x spread means we paired numbers
# from two different things (typo "$147,00"=14,700 vs 117,600 → 8x; or a
# garbled figure). Reject those rather than emit a nonsense range.
_MAX_RANGE_RATIO = 6.0


def _suffix_mult(suffix: str | None) -> int:
    if not suffix:
        return 1
    return 1_000_000 if suffix.upper() == "M" else 1_000  # K


def parse_compensation(
    summary: str | None,
) -> tuple[int | None, int | None, str | None, str | None]:
    """Parse a compensation string into ``(salary_min, salary_max, currency,
    period)``. Never raises; returns all-``None`` when nothing trustworthy is
    found.

    Robust against the failure modes seen feeding the full Greenhouse JD body
    (PR #80) rather than a clean comp summary:
      * multi-currency JDs (a USD range AND a CAD range) - scoped to a single
        range, preferring USD, instead of pairing the USD floor with the CAD
        floor;
      * range ceilings that lack their own ``$`` ("$189,000-236,200");
      * garbled / typo'd figures ($142,400,000; "$147,00") - rejected by
        magnitude + range-ratio sanity checks;
      * always returns ``salary_min <= salary_max``.
    """
    if not summary or not summary.strip():
        return None, None, None, None

    s = summary.strip()
    hourly = bool(_COMP_HOURLY_RE.search(s))
    period = "hourly" if hourly else "annual"
    lo_bound, hi_bound = (_HOURLY_MIN, _HOURLY_MAX) if hourly else (_ANNUAL_MIN, _ANNUAL_MAX)

    # Candidate = (lo, hi, currency, is_range, order). Collect every parseable
    # range/value, then pick the best one (a real range, USD, earliest).
    candidates: list[tuple[int, int, str, bool, int]] = []
    for order, m in enumerate(_COMP_RANGE_RE.finditer(s)):
        try:
            floor = int(float(m.group("floor").replace(",", "")) * _suffix_mult(m.group("fsuf")))
        except ValueError:
            continue
        ceil_raw = m.group("ceil")
        is_range = ceil_raw is not None
        if is_range:
            try:
                ceil = int(float(ceil_raw.replace(",", "")) * _suffix_mult(m.group("csuf")))
            except ValueError:
                continue
        else:
            ceil = floor
        lo, hi = (floor, ceil) if floor <= ceil else (ceil, floor)

        # Currency for THIS candidate.
        glyph = m.group("glyph")
        if glyph == "£":
            currency = "GBP"
        elif glyph == "€":
            currency = "EUR"
        else:  # "$"
            trailing = s[m.end() : m.end() + 5].upper()
            currency = "CAD" if (m.group("lead") or "CAD" in trailing) else "USD"

        # Sanity: plausible magnitude + plausible spread.
        if lo < lo_bound or hi > hi_bound:
            continue
        if lo > 0 and hi / lo > _MAX_RANGE_RATIO:
            continue
        candidates.append((lo, hi, currency, is_range, order))

    if not candidates:
        return None, None, None, None

    # Prefer a real range over a lone number; then USD over other currencies;
    # then the earliest occurrence (the comp range usually leads the JD).
    candidates.sort(key=lambda c: (not c[3], c[2] != "USD", c[4]))
    lo, hi, currency, _is_range, _order = candidates[0]
    return lo, hi, currency, period
