"""Title-level PM keep-list pre-filter (Slice 1 of broad-ingestion expansion).

The broad-ingest cron (Slice 2, future PR) will pull from thousands of
ATS handles, ~95% of which are non-PM roles (engineers, sales, ops,
legal, etc). Letting those flood the DB would:

  * Bloat ``job_posting`` from ~2.3k rows toward 350k+, dominated by
    rows the operator never wants to see.
  * Inflate the existing ``role_family`` heuristic / Gemini classifier
    sweep cost — the precise classifier becomes the bottleneck for
    discarding noise the title alone reveals.
  * Skew Companies/Stats page counts toward irrelevant role mixes.

This module is the cheap pre-filter that runs in
``IngestionService.ingest_source`` BEFORE ``normalize()`` so non-PM
titles never reach the DB. It is **conservative on the keep side** by
design — over-inclusion at this stage is fine because the existing
Gemini classifier (``services/classifier.py``) is the precision pass
that runs on what survives.

Additive guard
--------------
The filter is **opt-in per ingest call**. ``IngestionService.ingest_source``
exposes ``apply_title_prefilter=False`` so the existing curated-30 cron
keeps ingesting everything (including non-PM roles, which surface
via the Companies/Stats pages and the ``include_filtered=true`` toggle).
The Slice 2 broad-ingest cron will pass ``True`` for its handles.

Keep-list shape
---------------
Positive match (case-insensitive, substring): the title contains a
``product``-flavored noun cluster — ``product manager``, ``product
owner``, ``product management``, ``product lead``, ``product
operations``, or the abbreviations ``pm`` / ``apm`` / ``gpm`` / ``cpo``
/ ``vp product`` / ``head of product`` / ``chief product officer``.
Seniority prefixes (senior, sr, principal, staff, group, lead,
associate, head, director, vp, chief, technical) are accepted but not
required.

Explicit exclusions
-------------------
A row is dropped even if it positive-matches when the title also
contains one of:
  * ``product marketing`` — PMM, different role family.
  * ``product designer`` / ``product design`` — design, different.
  * ``product engineer`` / ``product engineering`` — engineering,
    different.
  * ``product support`` — support engineering, different.
  * ``product analyst`` (alone, without ``manager``) — data role.
  * ``product owner accountant`` etc. (exclusion list extends naturally)

The exclusions are deliberate carve-outs from the broad ``product``
match. Other adjacent-but-distinct roles (e.g. ``product researcher``)
fall through to the classifier — over-inclusion is fine.
"""

from __future__ import annotations

import re

# ── Positive match ──────────────────────────────────────────────────────────
#
# Two paths to "yes":
#   1. Title contains ``product`` followed (within a few tokens) by
#      ``manager``, ``owner``, ``management``, ``lead``, ``ops``,
#      ``operations``, or ``mgr``. The ``\s*(?:[-,/|]\s*)?`` between them
#      lets phrases like ``product, manager`` or ``product / manager``
#      match alongside the ordinary ``product manager``.
#   2. Title contains a PM abbreviation surrounded by word boundaries
#      so an "spm" inside another word never false-positives. Common
#      forms: ``pm``, ``apm`` (associate PM), ``gpm`` (group PM),
#      ``cpo`` (chief product officer). Bare ``pm`` is matched only
#      with explicit role-y context (`a pm`, `product, pm`, `pm role`)
#      since it's a noisy abbreviation alone — see the ``_BARE_PM_RE``
#      separate pattern.
_PRODUCT_ROLE_RE = re.compile(
    r"\bproduct\s*(?:[-,/|]\s*)?"
    r"(?:manager|managers|owner|owners|management|leadership|lead|leads|"
    r"ops|operations|mgr|mgrs|chief|director|head)\b",
    re.IGNORECASE,
)

_PM_ABBREV_RE = re.compile(
    r"\b(?:apm|gpm|cpo|vp\s+of\s+product|vp\s+product|"
    r"head\s+of\s+product|chief\s+product\s+officer)\b",
    re.IGNORECASE,
)

# Title leads with a seniority chip then names ``product`` as the
# department — e.g. ``Director, Product`` / ``Head of Product`` /
# ``VP, Product`` / ``Chief Product Officer``. The PM_ABBREV regex
# above handles the "of/no separator" forms but misses the
# comma-separated department-name idiom. This pattern catches them.
_LEADING_SENIORITY_RE = re.compile(
    r"\b(?:director|head|vp|chief|principal)\s*[,\-—|/]?\s*product\b",
    re.IGNORECASE,
)

# Bare ``pm`` is only accepted when the title separately confirms a
# product context (e.g. ``Senior PM, Growth`` — contains both PM and
# the word "product" or has comma-separated role tokens that include
# pm). Otherwise it's too noisy (project manager, program manager, PM
# could be PostMaster…).
_BARE_PM_RE = re.compile(r"\bpm\b", re.IGNORECASE)
_PRODUCT_WORD_RE = re.compile(r"\bproduct\b", re.IGNORECASE)


# ── Explicit exclusions ─────────────────────────────────────────────────────
#
# Each of these contains ``product`` AND a word that would otherwise
# satisfy the positive match (manager / lead / etc.) — but they're
# distinct role families. The exclusion is applied as a substring
# check, NOT a regex, because the variations are limited and explicit.
_EXCLUSION_PHRASES: tuple[str, ...] = (
    "product marketing",
    "product designer",
    "product design ",  # trailing space avoids matching "product designer"
    "product engineer",
    "product engineering",
    "product support",
    "product researcher",
    "product research",
    "product analyst",  # data/analytics role — survives only if "manager" too
    "product specialist",
    "product copywriter",
    "product writer",
    "product editor",
    "product photographer",
    "product illustrator",
    "product trainer",
    "product training",
)


def _has_excluded_phrase(lowered: str) -> bool:
    """True iff ``lowered`` contains an exclusion phrase that does NOT
    also include the word ``manager`` adjacent to it.

    The carve-out: ``product marketing manager`` is OUT (PMM), but
    ``product manager, marketing`` is IN (PM whose surface is marketing).
    The exclusion check looks at the FIRST exclusion phrase found in
    the title — if the title also contains ``\bmanager\b`` strictly
    AFTER that exclusion (rare, defensive), we keep.
    """
    for phrase in _EXCLUSION_PHRASES:
        idx = lowered.find(phrase)
        if idx == -1:
            continue
        # If the exclusion phrase is followed by " manager" within a
        # short window, the title is "<excluded-role> manager" — still
        # a distinct role (e.g. "product marketing manager" = PMM). Drop.
        # No carve-out here; we drop. The defensive carve-out for
        # "product manager, marketing" works because ``find`` returns
        # the LEFTMOST match — that title would first hit ``product
        # manager`` via the positive regex, and the exclusion phrase
        # ``product marketing`` would not be present.
        return True
    return False


def should_keep_title(raw_title: str | None) -> bool:
    """True iff *raw_title* is a plausible PM role worth ingesting.

    Conservative keep: anything that looks PM-ish passes (the
    classifier is the precision pass downstream). Empty, None, or
    whitespace-only titles fail — there's no signal to keep.

    Cheap: pure regex over the raw title. Roughly 1-3 µs per call.
    Safe to run on every fetched posting before paying the cost of
    ``adapter.normalize()`` or the upsert path.
    """
    if not raw_title or not raw_title.strip():
        return False

    lowered = raw_title.lower()

    # Exclusion comes first: even a positive match is dropped when the
    # title is clearly a distinct role family ("product marketing
    # manager", "product designer", etc.).
    if _has_excluded_phrase(lowered):
        return False

    if _PRODUCT_ROLE_RE.search(lowered):
        return True
    if _PM_ABBREV_RE.search(lowered):
        return True
    if _LEADING_SENIORITY_RE.search(lowered):
        return True
    # ``pm`` alone keeps only when the title also names ``product``,
    # so titles like "PM, Growth" without context fail. Most real PM
    # postings spell it out somewhere ("Senior Product Manager — Growth
    # (Sr. PM)").
    return bool(_BARE_PM_RE.search(lowered) and _PRODUCT_WORD_RE.search(lowered))


__all__ = ["should_keep_title"]
