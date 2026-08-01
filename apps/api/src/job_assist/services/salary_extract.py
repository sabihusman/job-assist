"""Salary extraction from the jd_summary ``**Comp**`` block (D-SALARY-EXTRACT).

34 of 68 rows in the 2026-08-01 triage export carried no structured salary,
yet most had a real compensation band sitting in ``jd_summary_markdown``
under the ``**Comp**`` heading — the adapters' structured extraction misses
location-tiered lists (Capital One's per-city bullets, Drata's Tier 1/2/3)
and OTE phrasing (Notion). This module recovers those bands.

ONE pure function — ``parse_salary_from_summary`` — with a single
definition, imported by BOTH call sites (amendment constraint):

  * scoring: ``score_breakdown`` falls back to it when the structured
    salary columns are NULL, so ``score.salary`` reflects the parsed band;
  * export: ``postings_export`` derives ``salary_source`` / ``salary_known``
    / ``salary_parsed_min`` / ``salary_parsed_max`` from it.

OBSERVE-ONLY: nothing here writes to the DB — ``salary_min``/``salary_max``
stay NULL on the row; parsed values live only in the score computation and
the export-derived columns. No hard-rule enforcement reads these values.

MULTI-BAND POLICY (directive): when a Comp block carries several bands
(per-city bullets, Tier lists, inline "X in SF and Y in Chicago"), take the
band with the LOWEST minimum — and THAT band's maximum. The operator
relocates, so the cheapest-location band is the realistic one. Never merge
min-of-mins with max-of-maxes across different bands.

Reuses ``adapters/normalization.py``'s compensation regex + sanity bounds
(the ``_sha256``-style cross-module private import has precedent) so the
two parsers can't drift on what "a salary number" means. Hourly bands are
annualized at 2080 h/yr (``ANNUAL_HOURS``) and the result records that the
conversion happened (``annualized_from_hourly``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from job_assist.adapters.normalization import (
    _ANNUAL_MAX,
    _ANNUAL_MIN,
    _COMP_HOURLY_RE,
    _COMP_RANGE_RE,
    _HOURLY_MAX,
    _HOURLY_MIN,
    _MAX_RANGE_RATIO,
    _suffix_mult,
)
from job_assist.services.scoring import ANNUAL_HOURS

# The Comp heading the jd_summary_enrichment prompt emits, and the start of
# the NEXT bold heading (which terminates the block). The block is the only
# text this parser reads — comp figures elsewhere in the summary (e.g. a
# quoted bonus % in Scope) are deliberately out of reach.
# Review finding: the terminator matches ONLY the enrichment template's own
# section names (jd_summary_enrichment.py's **Scope**/**Org context**/...),
# not any bold line — an LLM that sub-labels the comp value ("**Base**:
# $150,000...") must not truncate the block to empty.
_COMP_HEADING_RE = re.compile(r"\*\*Comp\*\*:?", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(
    r"\n\s*\*\*(?:Scope|Org context|Hard requirements|Nice-to-haves|Location|Ambiguities)\*\*",
    re.IGNORECASE,
)

# Hourly phrasing that appears AFTER the band rather than between its numbers
# ("$32.22 - $39.19 per hour") — normalization's _COMP_HOURLY_RE only sees
# units consumed inside the match ("/hr"). Checked against a short window
# following each candidate.
_TRAILING_HOURLY_RE = re.compile(
    r"^\s*(?:per\s+hour|per\s+hr|hourly|/\s*(?:hr|hour))", re.IGNORECASE
)
_TRAILING_WINDOW_CHARS = 16

# "$107,450.00 USD - $199,550.00 USD" (John Hancock, live corpus): the
# literal " USD" token between the floor and the dash breaks _COMP_RANGE_RE's
# range continuation, splitting one band into two singles — the lowest-band
# policy then returns a collapsed min==max. The regex already requires a
# currency glyph, so a standalone USD token adds no information — strip it
# before matching. USD only: stripping CAD would break _currency_for's
# trailing-token CAD detection, and C$-prefixed bands pair fine as-is.
_USD_TOKEN_RE = re.compile(r"\s+USD\b")


@dataclass(frozen=True, slots=True)
class ParsedSalary:
    """One annualized band recovered from a Comp block.

    ``salary_min``/``salary_max`` are USD-or-stated-currency integers per
    year; ``annualized_from_hourly`` records a 2080 h/yr conversion so the
    number is traceable to its hourly source.
    """

    salary_min: int
    salary_max: int
    currency: str
    annualized_from_hourly: bool


def _extract_comp_block(jd_summary_markdown: str) -> str | None:
    m = _COMP_HEADING_RE.search(jd_summary_markdown)
    if m is None:
        return None
    tail = jd_summary_markdown[m.end() :]
    nxt = _NEXT_HEADING_RE.search(tail)
    return tail[: nxt.start()] if nxt else tail


def _currency_for(match: re.Match[str], block: str) -> str:
    glyph = match.group("glyph")
    if glyph == "£":
        return "GBP"
    if glyph == "€":
        return "EUR"
    # Review finding: normalization's raw 5-char substring check flips USD to
    # CAD when the next SENTENCE merely starts with "Cad..." ("$180,000.
    # Cadence: annual reviews"). Free LLM prose follows bands routinely, so
    # require a word-bounded CAD token in a slightly wider window.
    trailing = block[match.end() : match.end() + 8]
    return "CAD" if (match.group("lead") or re.search(r"\bCAD\b", trailing)) else "USD"


def parse_salary_from_summary(jd_summary_markdown: str | None) -> ParsedSalary | None:
    """Parse the ``**Comp**`` block into one annualized band, or ``None``.

    Handles: single bands ("$181,000 - $235,000 / year"), decimal forms
    ("$70,000.00"), K/M suffixes ("$250k - $280k"), multi-line per-location
    bullet lists, Tier N lists, inline multi-band sentences, OTE phrasing
    (the band parses the same; "OTE" is just surrounding prose), and hourly
    rates ("$32.22 - $39.19 per hour" → x2080, ``annualized_from_hourly``).

    "Not stated." / missing heading / no parseable figures → ``None``.
    Never raises. Pure function of its input.
    """
    if not jd_summary_markdown or not jd_summary_markdown.strip():
        return None
    block = _extract_comp_block(jd_summary_markdown)
    if block is None or not block.strip():
        return None
    block = _USD_TOKEN_RE.sub("", block)

    candidates: list[tuple[int, int, str, bool, bool, int]] = []
    for order, m in enumerate(_COMP_RANGE_RE.finditer(block)):
        match_text = m.group(0)
        trailing = block[m.end() : m.end() + _TRAILING_WINDOW_CHARS]
        hourly = bool(_COMP_HOURLY_RE.search(match_text)) or bool(
            _TRAILING_HOURLY_RE.search(trailing)
        )
        lo_bound, hi_bound = (_HOURLY_MIN, _HOURLY_MAX) if hourly else (_ANNUAL_MIN, _ANNUAL_MAX)
        try:
            floor = float(m.group("floor").replace(",", "")) * _suffix_mult(m.group("fsuf"))
        except ValueError:
            continue
        ceil_raw = m.group("ceil")
        is_range = ceil_raw is not None
        if is_range:
            try:
                ceil = float(ceil_raw.replace(",", "")) * _suffix_mult(m.group("csuf"))
            except ValueError:
                continue
        else:
            ceil = floor
        lo, hi = (floor, ceil) if floor <= ceil else (ceil, floor)

        # Sanity: plausible magnitude for the period, plausible spread —
        # identical checks to normalization.parse_compensation, so a "10%
        # bonus" or a garbled figure can't masquerade as a band.
        if lo < lo_bound or hi > hi_bound:
            continue
        if lo > 0 and hi / lo > _MAX_RANGE_RATIO:
            continue

        if hourly:
            lo, hi = lo * ANNUAL_HOURS, hi * ANNUAL_HOURS
        candidates.append((round(lo), round(hi), _currency_for(m, block), hourly, is_range, order))

    if not candidates:
        return None

    # No FX conversion: when USD bands exist alongside non-USD ones, compare
    # only within USD. (All-non-USD blocks fall through with their currency
    # recorded — score_salary treats non-USD as the neutral 60.)
    usd = [c for c in candidates if c[2] == "USD"]
    pool = usd if usd else candidates

    # Review finding (sign-on-bonus hijack): a lone $-figure ("$25,000
    # sign-on bonus", "$15,000 relocation") enters as a min==max single and
    # would win the lowest-min policy over the REAL band. Real ranges beat
    # single values whenever any range exists — the same precedence
    # normalization.parse_compensation applies via its `not is_range` sort
    # key. A single value only represents the posting when it's all there is
    # ("$210,000 base.").
    ranges = [c for c in pool if c[4]]
    pool = ranges if ranges else pool

    # LOWEST-band policy: lowest annualized min wins; that band's own max
    # rides along. Ties break to the lower max, then first occurrence.
    lo, hi, currency, hourly, _is_range, _order = min(pool, key=lambda c: (c[0], c[1], c[5]))
    return ParsedSalary(
        salary_min=lo,
        salary_max=hi,
        currency=currency,
        annualized_from_hourly=hourly,
    )


__all__ = ["ParsedSalary", "parse_salary_from_summary"]
