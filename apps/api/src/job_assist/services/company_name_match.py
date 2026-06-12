"""Company-name extraction + normalization for company-level signals
(feat/company-app-awareness).

The Gmail outcome history is the source of truth for "have I applied / been
rejected here", but most ``outcome_event`` rows are UNLINKED
(``target_company_id`` IS NULL) — the #162 domain matcher only links a minority,
and ``from_domain`` is the ATS vendor (greenhouse.io / ashbyhq.com), useless as a
company label. So the only place a real company name lives for the unlinked
majority is the email SUBJECT ("Thank you for applying to <Company>").

This module ports the frontend ``companyFromSubject`` extraction (kept in
lockstep with ``apps/web/src/lib/pipeline/companyFromSubject.ts``) to the backend
and adds:

  * ``normalize_company_name`` — a case/suffix/punctuation-insensitive key so
    "Stripe, Inc." and "stripe" collapse to the same company.
  * ``ambiguous_keys`` — the no-false-badge guard: when one normalized key's
    tokens are a proper subset of another's (e.g. "John Hancock" ⊊ "Manulife
    John Hancock"), we CANNOT tell whether the shorter-named emails belong to the
    standalone company or the compound parent, so BOTH keys are suppressed. A
    false count is worse than no count.
"""

from __future__ import annotations

import re

# "...applying to/at/with <X>" or "...application to/with <X>". Anchored on the
# apply verb (not the start), case-insensitive; captures the trailing remainder
# which ``_clean_company`` then trims down. "for" is deliberately excluded —
# "applying for the <role>" is about the role, not the company.
_APPLY_RE = re.compile(r"appl(?:ying|ication)\s+(?:to|at|with)\s+(.+)$", re.IGNORECASE)

# Possessive lead: "<X>'s Recruiting Team", "<X>'s hiring team". The character
# class matches both straight and curly apostrophes (mirrors the TS original).
#
# fix(audit): the old lazy ``^(.+?)`` expanded across ANY leading words to the
# first apostrophe-s anywhere — "An update from Acme's Recruiting Team"
# captured "An update from Acme" and "Your application's status…" captured
# "Your application", shipping junk company labels on the exact vague subjects
# this fallback exists for. The prefix is now a short run (≤4) of
# capitalized/numeric tokens anchored at the start — a sentence-y lead
# ("An update…", "Your application…") fails the run and falls through to the
# from_domain fallback instead.
_POSSESSIVE_RE = re.compile(
    r"^((?:[A-Z0-9][\w&.-]*\s+){0,3}[A-Z0-9][\w&.-]*)['’`]s\s+\S+"  # noqa: RUF001
)

# Legal-entity suffix tokens dropped during normalization so "Stripe, Inc." and
# "Stripe" collapse. Conservative on purpose — "Technologies" / "Labs" / "Group"
# are NOT dropped (they distinguish real, different companies, e.g. "Covr
# Financial Technologies").
_SUFFIX_TOKENS = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "plc",
        "sa",
        "ag",
        "nv",
        "bv",
        "llp",
        "lp",
        "pty",
    }
)

# ATS / job-board vendor tokens. If an extracted "company" is really one of these
# (e.g. the subject degenerated to the ATS domain), it's not a company — drop it
# so we never badge "applications at greenhouse".
_VENDOR_TOKENS = frozenset(
    {
        "greenhouse",
        "greenhouseio",
        "lever",
        "leverco",
        "ashby",
        "ashbyhq",
        "workday",
        "myworkday",
        "myworkdayjobs",
        "icims",
        "jobvite",
        "smartrecruiters",
        "workable",
        "breezy",
        "breezyhr",
        "bamboohr",
        # fix(audit): "no-reply" deleted — _tokens() strips all non-
        # alphanumerics, so a token can never contain a hyphen and the entry
        # was unreachable dead weight ("noreply"/"donotreply" do the work).
        "noreply",
        "donotreply",
    }
)


def company_from_subject(subject: str | None) -> str | None:
    """Derive a company name from an ATS confirmation/rejection subject.

    Returns the extracted company, or ``None`` when the subject is generic
    ("Update on Your Application") so the caller can fall back. Mirrors the
    frontend ``companyFromSubject``.
    """
    if not subject:
        return None
    s = subject.strip()
    if not s:
        return None

    apply_match = _APPLY_RE.search(s)
    candidate: str | None = apply_match.group(1) if apply_match else None

    if candidate is None:
        poss = _POSSESSIVE_RE.search(s)
        if poss:
            candidate = poss.group(1)

    return _clean_company(candidate) if candidate else None


def _clean_company(raw: str) -> str | None:
    c = raw.strip()

    # Cut role/qualifier tails at a separator: "Acme - Senior PM" -> "Acme",
    # "Acme: Reqs" -> "Acme". fix(audit): leading whitespace is OPTIONAL —
    # the old \s+ prefix meant "Acme: Senior PM" (no space before the colon,
    # the common ATS shape) never split and the full string shipped as the
    # company label. Trailing \s+ stays required so hyphenated names
    # ("Coca-Cola") don't split.
    c = re.split(r"\s*[-–—|:]\s+", c)[0].strip()  # noqa: RUF001

    # "Acme for the Product Manager role" → "Acme".
    c = re.sub(r"\s+for\s+(?:the\s+|our\s+|a\s+)?.*$", "", c, flags=re.IGNORECASE)

    # Drop a trailing role/qualifier noun and anything after it.
    c = re.sub(
        r"\s+(position|role|opening|opportunity|req(?:uisition)?)\b.*$",
        "",
        c,
        flags=re.IGNORECASE,
    )

    # Trailing punctuation / whitespace ("Uphold!" -> "Uphold").
    c = re.sub(r"[\s!.,;:–—-]+$", "", c).strip()  # noqa: RUF001

    # Leading article.
    c = re.sub(r"^the\s+", "", c, flags=re.IGNORECASE).strip()

    return c if c else None


# TLD-ish noise tokens dropped so a bare ATS domain ("greenhouse.io",
# "ashbyhq.com") reduces to its vendor token and is then rejected as a non-company.
_TLD_TOKENS = frozenset({"com", "io", "net", "org"})

_DROP_TOKENS = _SUFFIX_TOKENS | _TLD_TOKENS


def _tokens(name: str) -> list[str]:
    """Lowercase, strip punctuation, drop legal-suffix + TLD tokens."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return [t for t in cleaned.split() if t and t not in _DROP_TOKENS]


def normalize_company_name(name: str | None) -> str | None:
    """Return a stable normalized key for a company name, or ``None`` when the
    name is empty, all-suffix, or an ATS/vendor token (not a real company).

    "Stripe, Inc." / "STRIPE" / "stripe" → ``"stripe"``.
    "greenhouse.io" → ``None`` (vendor).
    """
    if not name:
        return None
    toks = _tokens(name)
    if not toks:
        return None
    # All-vendor (e.g. "greenhouse", "myworkday") → not a company.
    if all(t in _VENDOR_TOKENS for t in toks):
        return None
    return " ".join(toks)


def normalized_token_set(key: str) -> frozenset[str]:
    """Token set of an already-normalized key (for ambiguity comparison)."""
    return frozenset(key.split())


def ambiguous_keys(keys: set[str]) -> set[str]:
    """Given a set of normalized keys, return the subset that is AMBIGUOUS and
    must be suppressed.

    A key is ambiguous when its token set is a PROPER subset of another key's
    token set — we can't tell whether the shorter name's emails belong to the
    standalone company or the longer (compound / parent) one. Both the subset and
    its superset are suppressed, since attributing the shorter-named events to
    either would risk a wrong count.

    Example: {"john hancock", "manulife john hancock"} → both suppressed.
    """
    token_sets = {k: normalized_token_set(k) for k in keys}
    ambiguous: set[str] = set()
    for a, ta in token_sets.items():
        for b, tb in token_sets.items():
            if a is b or a == b:
                continue
            # ta ⊊ tb  → a is a subset of b: ambiguous on both sides.
            if ta < tb:
                ambiguous.add(a)
                ambiguous.add(b)
    return ambiguous


__all__ = [
    "ambiguous_keys",
    "company_from_subject",
    "normalize_company_name",
    "normalized_token_set",
]
