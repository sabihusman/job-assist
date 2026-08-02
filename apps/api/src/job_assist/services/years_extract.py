"""Years-of-experience extraction from the jd_summary ``**Hard requirements**``
block (D5-OBSERVE).

OBSERVE-ONLY: nothing here changes any score. Senior roles behind unleveled
titles currently score seniority 100 (MSCI "15+ years" → title-leveled ``pm``
→ in the operator's included set) because nothing consumes the Hard
requirements block. This module is the DETECTOR half only — it extracts and
surfaces the years bar so the real distribution of qualifiers can be seen
before any capping policy is designed. The cap (and the operator-profile
fields it needs: max_years_bar_tolerated, degrees_held) lands in a separate
directive.

ONE pure function — ``parse_years_requirement`` — single definition, imported
by the export path (mirrors ``services/salary_extract.py``). Export-derived
columns only; no DB writes, no migration.

Parsing rules (directive-pinned; deliberately strict):
  * "N+ years", "minimum of N years", "at least N years" → N
  * "N-M years" → N (the LOWER bound; "5-8 years" yields 5)
  * multiple clauses → the LOWEST N wins, and THAT clause's qualifier text
    becomes ``years_domain``
  * no matching clause → ``None`` (bare "N years of ..." intentionally does
    NOT match this pass — unmatched apparent clauses are reported, not
    silently absorbed)

``years_domain`` is captured VERBATIM (the text following "years" up to the
end of its clause) — no classification or normalization this pass; the point
is to see what the real distribution looks like (directive rule d from the
scorer bug report: "3+ years of FINANCIAL SERVICES experience" must be
distinguishable from "3 years of Product Management experience" downstream).
"""

from __future__ import annotations

import re
from typing import TypedDict


class YearsRequirement(TypedDict):
    """The parsed years bar of one Hard requirements block."""

    min_years_required: int
    years_domain: str
    education_substitution_available: bool
    source_clause: str


# The Hard requirements heading the jd_summary_enrichment prompt emits, and
# the template's OTHER section names as the block terminator (same approach
# as salary_extract's Comp-block extraction — a bold sub-label inside the
# block must not truncate it).
_HARD_REQ_HEADING_RE = re.compile(r"\*\*Hard requirements\*\*:?", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(
    r"\n\s*\*\*(?:Scope|Org context|Nice-to-haves|Comp|Location|Ambiguities)\*\*",
    re.IGNORECASE,
)

# The three directive-pinned clause forms. Each pattern's group "n" is the
# minimum. Ranges capture the LOWER bound. "years?" covers "1+ year".
_YEARS_CLAUSE_RES: tuple[re.Pattern[str], ...] = (
    # "5-8 years" (hyphen or en dash U+2013) - FIRST so the range's lower bound wins the
    # position (the other patterns have no bare-N form, so no double-count).
    re.compile(r"\b(?P<n>\d{1,2})\s*[-\u2013]\s*\d{1,2}\s+years?\b", re.IGNORECASE),
    # "15+ years"
    re.compile(r"\b(?P<n>\d{1,2})\s*\+\s*years?\b", re.IGNORECASE),
    # "minimum of 5 years"
    re.compile(r"\bminimum of\s+(?P<n>\d{1,2})\s+years?\b", re.IGNORECASE),
    # "at least 3 years"
    re.compile(r"\bat least\s+(?P<n>\d{1,2})\s+years?\b", re.IGNORECASE),
)

# Education-substitution: an " OR " (uppercase, the alternative-branch marker
# in real blocks — "..., OR a Bachelor's degree in any field and at least 3
# years...") or a literal "or at least" joining a degree mention to a years
# mention. Scoped inline (?i:) groups keep the degree/years parts
# case-insensitive while the OR token itself stays case-sensitive, so prose
# commas ("Business, or Marketing") don't false-positive on their own.
_EDU_OR_UPPER_RE = re.compile(
    r"(?i:bachelor|master(?:'s)?|mba|ph\.?d|degree)"
    r"[\s\S]{0,300}?\bOR\b[\s\S]{0,300}?"
    r"(?i:\d{1,2}\s*\+?\s*years?)"
)
_EDU_OR_AT_LEAST_RE = re.compile(
    r"(?:bachelor|master(?:'s)?|mba|ph\.?d|degree)"
    r"[\s\S]{0,300}?\bor at least\s+\d{1,2}\s+years?",
    re.IGNORECASE,
)

# Domain qualifier: verbatim text following the matched clause's "years",
# up to the end of the clause (sentence stop, newline/bullet end).
_DOMAIN_STOP_RE = re.compile(r"[.\n]")


def _extract_hard_req_block(jd_summary_markdown: str) -> str | None:
    m = _HARD_REQ_HEADING_RE.search(jd_summary_markdown)
    if m is None:
        return None
    tail = jd_summary_markdown[m.end() :]
    nxt = _NEXT_HEADING_RE.search(tail)
    return tail[: nxt.start()] if nxt else tail


def _clause_of(block: str, start: int, end: int) -> str:
    """The full line (bullet) containing the match — for auditing."""
    line_start = block.rfind("\n", 0, start) + 1
    line_end = block.find("\n", end)
    if line_end == -1:
        line_end = len(block)
    return block[line_start:line_end].strip().lstrip("*").strip()


def _domain_after(block: str, years_end: int) -> str:
    """Verbatim qualifier text following the clause's "years" token."""
    tail = block[years_end:]
    stop = _DOMAIN_STOP_RE.search(tail)
    domain = tail[: stop.start()] if stop else tail
    return domain.strip()


def parse_years_requirement(jd_summary_markdown: str | None) -> YearsRequirement | None:
    """Parse the Hard requirements block's years bar, or ``None``.

    Pure function, never raises. Returns ``None`` when the block is absent
    or carries no clause in one of the three pinned forms — years stated
    elsewhere in the summary are deliberately NOT inferred.
    """
    if not jd_summary_markdown or not jd_summary_markdown.strip():
        return None
    block = _extract_hard_req_block(jd_summary_markdown)
    if block is None or not block.strip():
        return None

    # Collect every clause across the pinned forms; overlapping spans (a
    # range's bare tail, etc.) can't double-count because no bare-N form
    # exists. Lowest N wins; ties break to the earliest occurrence.
    found: list[tuple[int, int, int]] = []  # (n, match_start, match_end)
    for pattern in _YEARS_CLAUSE_RES:
        for m in pattern.finditer(block):
            found.append((int(m.group("n")), m.start(), m.end()))
    if not found:
        return None
    n, start, end = min(found, key=lambda t: (t[0], t[1]))

    return YearsRequirement(
        min_years_required=n,
        years_domain=_domain_after(block, end),
        education_substitution_available=bool(
            _EDU_OR_UPPER_RE.search(block) or _EDU_OR_AT_LEAST_RE.search(block)
        ),
        source_clause=_clause_of(block, start, end),
    )


__all__ = ["YearsRequirement", "parse_years_requirement"]
