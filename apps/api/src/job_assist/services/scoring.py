"""Heuristic fit-scoring service (PR #56; semantic blend slice 2b).

Every JobPosting gets a composite 0-100 score derived from six weighted
features compared against the OperatorProfile. The output is a pure
function of (posting, profile) — deterministic, interpretable, no LLM
call, no I/O. The composite score is materialized on
``job_posting.fit_score`` so the "Best fit" sort reads it index-backed.

Features and weights
────────────────────
  role_family    20%  — posting.role_family vs PREFERRED_FAMILIES set
  seniority      20%  — posting.seniority_level vs profile.seniority_levels_included
  salary         15%  — posting.salary_min/max vs profile.salary_floor/ceiling
  tier           10%  — target_company.tier (T1=best)
  geo            15%  — posting.locations_normalized vs profile.geo_whitelist
  semantic_fit   20%  — calibrated similarity of the JD to the operator's
                        ``looking_for_text`` (reads the precomputed
                        ``job_posting.similarity_score``; see below)

Each feature returns an integer 0-100; the composite is the weighted MEAN
over the AVAILABLE features (so a not-yet-embedded posting, whose
``semantic_fit`` is absent, scores on the structured five alone — the weight
renormalizes, no fake signal). Then the role_family hard gate and the
disguised-senior cap apply.

Semantic feature & determinism (slice 2b)
─────────────────────────────────────────
``semantic_fit`` reads ``job_posting.similarity_score`` — the calibrated
0-100 PERCENT_RANK of this posting's cosine to ``profile.looking_for_embedding``
across the corpus, materialized by ``services/embeddings.recalibrate_similarity``
(one deterministic SQL pass). The vector model (``gemini-embedding-001``,
768-dim, L2-normalized) is pinned and version-stamped per row, and the
percentile calibration is deterministic, so ``score_posting`` stays a pure
function of its inputs. ``similarity_score`` is recomputed — and fit_score
re-scored — on the embedding-sweep tail and the profile-save hook, so a
profile-text edit actually moves scores. ``SCORER_VERSION`` is bumped so a
re-score is attributable.

Mock seam
─────────
``score_posting`` is a top-level module-level synchronous function.
Tests monkey-patch it via::

    monkeypatch.setattr(
        "job_assist.services.scoring.score_posting",
        stub,
    )

The function is sync (no async) — there's no I/O. The ingest path and
sweep endpoint call it inside their async transaction loops.

Bestiary note (PR #56): a scoring failure must NEVER cascade to fail
an ingest run or a classifier sweep. Callers wrap ``score_posting`` in
``try / except Exception`` and log + skip on error. The score is
optional decoration, not load-bearing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from job_assist.db.enums import RoleFamily, SeniorityLevel
from job_assist.db.models.job_posting import JobPosting
from job_assist.db.models.operator_profile import OperatorProfile

logger = logging.getLogger(__name__)

# Role-family hard gate cap (Bestiary 5.21). Named for the decomposition; the
# value is unchanged from the historical inline literal.
ROLE_GATE_CAP = 40

# business_analyst/financial_analyst expansion: analyst families are
# acceptable-but-discounted — NOT hard-gated to ROLE_GATE_CAP like a true
# non-PM family, but capped below the uncapped PREFERRED_FAMILIES ceiling so
# they can't outrank a genuine PM/PO role at the same structured score.
ANALYST_GATE_CAP = 85


# ── Version constant ─────────────────────────────────────────────────────────

SCORER_VERSION = "v2_semantic"


# ── Composite weights (sum = 100) ────────────────────────────────────────────
#
# slice 2b: ``semantic_fit`` joins the structured five. Trimmed tier 15→10 and
# geo 20→15, role_family/seniority 25→20 to make room for the 20% profile-fit
# signal. role_family stays gated (hard cap at 40) so its weight reduction
# doesn't let wrong-role postings ride the composite up.
_WEIGHTS: dict[str, int] = {
    "role_family": 20,
    "seniority": 20,
    "salary": 15,
    "tier": 10,
    "geo": 15,
    "semantic_fit": 20,
}
assert sum(_WEIGHTS.values()) == 100, "scoring weights must sum to 100"


# ── role_family preference (PR #56 Decision A2) ──────────────────────────────
#
# No ``preferred_role_families`` column exists on operator_profile yet.
# The codebase's implicit premise — surfaced in the classifier prompt,
# the OperatorProfile seed comments, and the README — is that this is a
# PM job-search tool. The set below encodes that premise as named
# constants rather than inline literals in the extractor, so the day
# the operator wants to broaden / narrow it, the diff is one location.
#
# Future PR can add ``preferred_role_families: list[str]`` to
# operator_profile, default this set, and the extractor reads from the
# row instead of from these constants. Until then: hardcoded.
PREFERRED_FAMILIES: frozenset[str] = frozenset(
    {
        RoleFamily.product_management.value,
        RoleFamily.product_owner.value,
    }
)
ADJACENT_FAMILIES: frozenset[str] = frozenset(
    {
        RoleFamily.product_marketing.value,
        RoleFamily.program_management.value,
    }
)

# business_analyst/financial_analyst expansion: acceptable-but-discounted.
# Distinct from ADJACENT_FAMILIES — analyst roles get their own sub-score
# (75, between ADJACENT's 60 and PREFERRED's 100) AND their own composite
# ceiling (ANALYST_GATE_CAP, not ROLE_GATE_CAP) in the hard-gate section
# below, rather than riding the ADJACENT weighted contribution alone.
ANALYST_FAMILIES: frozenset[str] = frozenset(
    {
        RoleFamily.business_analyst.value,
        RoleFamily.financial_analyst.value,
    }
)

# ── Disguised-senior altitude correction (career-changer profile) ────────────
# A product_management posting bucketed ``pm`` or ``unknown`` whose USD
# salary FLOOR is >= this threshold is almost always a mislabeled
# mid-senior role — the comp floor betrays the altitude the title/parser
# under-leveled. The operator (no PM title) can't land these cold, so
# ``score_posting`` caps them. SOFT cap (not exclusion) to spare the rare
# genuinely-junior-but-well-paid role: 55 sits below the 60 "good" and 80
# "qualified" lines (drops out of Best Fit top bands) but above the 40
# non-PM floor (stays visible, recoverable).
_DISGUISED_SENIOR_SALARY_FLOOR_USD = 175_000
_DISGUISED_SENIOR_CAP = 55
# Only these seniority buckets can hide a senior role — ``apm``/``intern``
# are explicitly junior (the operator wants them) and aren't where
# seniors disguise; ``senior_pm``/``lead_pm`` are handled by the seniority
# hard rule, not here.
_DISGUISED_SENIOR_SENIORITY: frozenset[str] = frozenset({"pm", "unknown"})


# ── Salary normalization (PR #56 Decision C) ─────────────────────────────────
#
# US full-time standard: 40 hr/week * 52 weeks = 2080 hours/year. Used to
# annualize hourly postings before comparing against the operator's
# annual band. Also a reasonable hook if posting hours/week ever gets
# parsed.
ANNUAL_HOURS = 2080


# ── PM ladder ordering (PR #56 Decision B1) ──────────────────────────────────
#
# Listed in ascending order so adjacency math is index-based. ``unknown``
# is treated as a neutral middle position rather than a step on the
# ladder — postings with NULL seniority surface for triage rather than
# get scored harshly.
_SENIORITY_LADDER: tuple[str, ...] = (
    SeniorityLevel.intern.value,
    SeniorityLevel.apm.value,
    SeniorityLevel.pm.value,
    SeniorityLevel.senior_pm.value,
    SeniorityLevel.lead_pm.value,
    SeniorityLevel.principal_pm.value,
)


# ── Bucket scores ────────────────────────────────────────────────────────────


def score_role_family(posting_family: str | None) -> int:
    """Match posting's role_family against PREFERRED_FAMILIES.

    Returns:
      100 — in PREFERRED_FAMILIES
       75 — in ANALYST_FAMILIES (business_analyst / financial_analyst —
            acceptable-but-discounted)
       60 — in ADJACENT_FAMILIES
       10 — ``other`` (hard penalty — likely a non-PM role mis-classified)
       40 — any other value (defensive — shouldn't happen given the enum)
    """
    if posting_family is None:
        # Shouldn't happen — role_family is NOT NULL on job_posting. Defensive.
        return 40
    value = str(posting_family)
    if value in PREFERRED_FAMILIES:
        return 100
    if value in ANALYST_FAMILIES:
        return 75
    if value in ADJACENT_FAMILIES:
        return 60
    if value == RoleFamily.other.value:
        return 10
    return 40


def score_seniority(
    posting_seniority: str | None,
    included_levels: list[str] | None,
) -> int:
    """Match posting's seniority_level against the operator's inclusion filter.

    Returns:
      70 — operator has no preference (NULL or empty list)
      50 — posting's seniority is ``unknown`` (neutral; surfaces for triage)
     100 — posting's seniority IS in the included set
      30 — posting's seniority is NOT in the included set

    The hard-rule filter (PR #43) already drops out-of-band seniority at
    ingest time when the operator sets this filter, so the 30 here is
    a "still surfaced for some reason" signal rather than a "hidden
    forever" penalty.
    """
    if posting_seniority is None:
        return 50
    value = str(posting_seniority)
    if value == SeniorityLevel.unknown.value:
        return 50
    if not included_levels:
        return 70
    if value in included_levels:
        return 100
    return 30


def _annualize_salary(value: int | None, period: str | None) -> int | None:
    """Convert ``value`` to USD/year given its period. Returns None when
    we can't trust the conversion."""
    if value is None or value <= 0:
        return None
    p = (period or "").lower()
    if p == "annual" or p == "unknown" or p == "":
        # Treat unknown as annual — the dominant case in the corpus.
        # If we're wrong, the operator can re-rank by inspection.
        return value
    if p == "hourly":
        return value * ANNUAL_HOURS
    # Unrecognised period — neutral skip.
    return None


def score_salary(
    salary_min: int | None,
    salary_max: int | None,
    salary_currency: str | None,
    salary_period: str | None,
    floor_usd: int,
    ceiling_usd: int | None,
) -> int:
    """Score the posting's salary against the operator's band.

    Returns:
       60 — NULL salary OR non-USD currency (we don't FX-convert)
      100 — annualized value lies inside the band
       80 — annualized value lies above the ceiling (over-paying is rarely a reason to skip)
       30 — annualized value lies below the floor

    Uses ``salary_max`` when available, falling back to ``salary_min``.
    """
    # Non-USD: skip the comparison. Most postings will be USD or NULL.
    if salary_currency and salary_currency.upper() != "USD":
        return 60
    raw = salary_max if salary_max is not None else salary_min
    annual = _annualize_salary(raw, salary_period)
    if annual is None:
        return 60
    if annual < floor_usd:
        return 30
    if ceiling_usd is not None and annual > ceiling_usd:
        return 80
    return 100


def score_tier(tier: int | None) -> int:
    """Score the target_company tier. T1 = best.

    Returns:
      100 / 80 / 60 / 40 for T1 / T2 / T3 / T4
       50 for NULL (posting has no matched target_company)
    """
    if tier is None:
        return 50
    if tier == 1:
        return 100
    if tier == 2:
        return 80
    if tier == 3:
        return 60
    if tier == 4:
        return 40
    # Out-of-range tier — neutral.
    return 50


def display_tier(company_tier: int | None, fit_score: int | None) -> int | None:
    """Coalesce a posting's DISPLAY tier (Slice 3, Part D Option 1).

    Curated companies carry a hand-assigned pedigree tier (1-4) — that
    always wins. Broad-discovered shells have ``tier=NULL``; for them
    the display tier is DERIVED from the posting's ``fit_score`` so the
    UI shows a meaningful chip instead of defaulting to T4.

    This is **display-only** — it does NOT feed scoring. ``score_tier``
    still maps NULL→50 as the scoring input; this function only decides
    which tier badge the operator sees. Inverse of ``score_tier``'s
    bands:

        fit_score 80-100 -> tier 1
                  60-79  -> tier 2
                  40-59  -> tier 3
                  <40    -> tier 4
        NULL score       -> None (nothing to derive from)

    Returns the company tier when set; else the score-derived band; else
    None (only when both company_tier AND fit_score are NULL — a posting
    at a shell the score sweep hasn't visited yet).
    """
    if company_tier is not None:
        return company_tier
    if fit_score is None:
        return None
    if fit_score >= 80:
        return 1
    if fit_score >= 60:
        return 2
    if fit_score >= 40:
        return 3
    return 4


def _location_strings(locations_normalized: Any) -> list[str]:
    """Extract a flat list of strings to compare against geo_whitelist.

    ``locations_normalized`` is a JSONB list of dicts shaped like
    ``{remote_type, city?, state?, country?}`` (see parse_location in
    adapters/normalization.py). We emit the city, state, "Remote"
    sentinel, and combined "city, state" forms — case-insensitive
    comparison happens at the call site.
    """
    if not isinstance(locations_normalized, list):
        return []
    out: list[str] = []
    for entry in locations_normalized:
        if not isinstance(entry, dict):
            continue
        if entry.get("remote_type") == "remote":
            out.append("Remote")
        city = entry.get("city")
        state = entry.get("state")
        if isinstance(city, str) and city.strip():
            out.append(city.strip())
        if isinstance(state, str) and state.strip():
            out.append(state.strip())
        if isinstance(city, str) and isinstance(state, str) and city.strip() and state.strip():
            out.append(f"{city.strip()}, {state.strip()}")
    return out


def score_geo(
    locations_normalized: Any,
    geo_whitelist: list[str],
) -> int:
    """Match any of the posting's normalized locations against the operator's whitelist.

    Returns:
      100 — at least one location string matches a whitelist entry
            (case-insensitive substring; handles "NYC" matching
            "New York, NY" and vice versa via two-sided substring check)
       50 — posting has no parseable locations (locations_normalized
            empty / missing)
       30 — has locations but none match the whitelist
    """
    posting_strings = _location_strings(locations_normalized)
    if not posting_strings:
        return 50
    if not geo_whitelist:
        # Operator hasn't set any preferences — every posting passes neutrally.
        return 70
    # Case-insensitive substring on both sides — operator's "NYC" matches
    # posting's "New York" if either substring appears in the other. Cheap
    # heuristic; if it produces false positives in practice the operator
    # can refine the whitelist strings.
    lowered_whitelist = [w.strip().lower() for w in geo_whitelist if w and w.strip()]
    lowered_posting = [p.strip().lower() for p in posting_strings if p.strip()]
    for w in lowered_whitelist:
        for p in lowered_posting:
            if w in p or p in w:
                return 100
    return 30


# ── Composite ───────────────────────────────────────────────────────────────


def is_disguised_senior(posting: JobPosting) -> bool:
    """True when a posting looks like a mislabeled mid-senior PM role.

    A ``product_management`` posting bucketed ``pm`` or ``unknown`` whose
    USD salary FLOOR (``salary_min``) is >= $175k is almost always a
    senior+ role the title/seniority parser under-leveled — the comp
    floor betrays the altitude. ``score_posting`` caps these (career-
    changer correction). (Floor retuned $180k → $175k in #158; the
    constant ``_DISGUISED_SENIOR_SALARY_FLOOR_USD`` + its test are the
    source of truth.)

    Precision choices to avoid false-positives:
      * FLOOR not max — a ``$130k-$175k`` band has min 130 (plausibly
        mid) and is NOT flagged; a ``$175k-$250k`` band has min 175 and
        IS. The minimum being senior-level is the strong signal.
      * Requires PARSED USD comp — unparsed / non-USD never flags (we
        don't FX-convert and won't guess).
      * Scoped to ``pm``/``unknown`` seniority — ``apm``/``intern`` are
        explicitly junior and wanted; ``senior_pm``/``lead_pm`` are
        excluded upstream by the seniority hard rule.

    ``seniority_level`` NULL is treated as ``unknown`` (a NULL-seniority
    PM at a senior comp floor is a prime disguise).
    """
    if str(posting.role_family) != RoleFamily.product_management.value:
        return False
    seniority = str(posting.seniority_level) if posting.seniority_level is not None else "unknown"
    if seniority not in _DISGUISED_SENIOR_SENIORITY:
        return False
    if posting.salary_currency != "USD":
        return False
    return (
        posting.salary_min is not None and posting.salary_min >= _DISGUISED_SENIOR_SALARY_FLOOR_USD
    )


def score_semantic_fit(similarity_score: int | None) -> int | None:
    """Profile-fit sub-score from the precomputed semantic similarity (slice 2b).

    ``job_posting.similarity_score`` is the calibrated 0-100 PERCENT_RANK of the
    posting's cosine similarity to ``profile.looking_for_embedding`` across the
    corpus (``services/embeddings.recalibrate_similarity`` — one deterministic
    SQL pass, re-run on the embedding-sweep tail + the profile-save hook).
    Reading the precomputed column keeps ``score_posting`` pure / no-I/O /
    deterministic.

    Returns ``None`` when the row isn't calibrated yet (posting or profile not
    embedded). ``score_posting`` then OMITS this feature and renormalizes over
    the remaining weights, so a not-yet-embedded posting scores on the
    structured features alone — no fake semantic signal.
    """
    if similarity_score is None:
        return None
    return max(0, min(100, int(similarity_score)))


def score_breakdown(
    posting: JobPosting,
    profile: OperatorProfile,
    *,
    tier: int | None,
) -> dict[str, int | bool | None]:
    """Return the six sub-scores plus the ``disguised_senior`` flag.

    The integer sub-scores feed the weighted composite; ``semantic_fit`` is
    ``None`` until the row is embedded + calibrated (then it's 0-100). The
    boolean ``disguised_senior`` is a debug/surface flag (NOT a weighted
    feature) — ``score_posting`` applies it as a post-composite cap, the
    same shape as the role_family gate. Useful for a "why this score" UI.

    ``tier`` is passed in explicitly because it lives on
    ``target_company``, not on ``job_posting`` — the caller resolves it
    once (and may pass ``None`` if the posting has no matched company).
    """
    return {
        "role_family": score_role_family(
            str(posting.role_family) if posting.role_family is not None else None
        ),
        "seniority": score_seniority(
            str(posting.seniority_level) if posting.seniority_level is not None else None,
            profile.seniority_levels_included,
        ),
        "salary": score_salary(
            posting.salary_min,
            posting.salary_max,
            posting.salary_currency,
            str(posting.salary_period) if posting.salary_period is not None else None,
            profile.salary_floor_usd,
            profile.salary_ceiling_usd,
        ),
        "tier": score_tier(tier),
        "geo": score_geo(
            posting.locations_normalized,
            profile.geo_whitelist or [],
        ),
        "semantic_fit": score_semantic_fit(posting.similarity_score),
        "disguised_senior": is_disguised_senior(posting),
    }


# ── Phase A3: applied-corpus boost (Philosophy 2 — surgical, lift-only) ───────
# Max points the boost can add at full weight x full confidence x full
# similarity. The blend is bounded by this AND by ``applied_corpus_weight`` AND
# by the confidence factor f(n) below.
MAX_APPLIED_BOOST = 10.0
# Upper anchor of the similarity ramp: sim at/above this gets the full fraction.
# The lower anchor is the corpus's own reference_band (carried on AppliedBasis).
_APPLIED_SIM_HI = 0.92


@dataclass(frozen=True, slots=True)
class AppliedBasis:
    """Corpus-level inputs for the applied-corpus boost, computed ONCE per sweep
    (NOT per posting) and injected into the pure scorer so it stays I/O-free.

    ``centroid`` is the mean of the applied (non-gated) jd_embeddings;
    ``centroid_norm`` is |centroid| (precomputed for cosine); ``reference_band``
    is the avg cosine of the basis to its own centroid (the lower ramp anchor);
    ``n`` is the basis size (confidence). Built by
    services/applied_corpus.load_applied_basis.
    """

    centroid: list[float]
    centroid_norm: float
    reference_band: float
    n: int


def _applied_confidence(n: int) -> float:
    """f(n) = min(1, n/30): thin corpus → fractional weight; grows with n."""
    return min(1.0, n / 30.0)


def _cosine_to_centroid(vec: list[float], basis: AppliedBasis) -> float:
    """Cosine of a (unit-norm) posting embedding to the centroid.

    jd_embedding is L2-normalized, centroid is not, so cos = dot / |centroid|.
    """
    if basis.centroid_norm <= 0:
        return 0.0
    return sum(v * c for v, c in zip(vec, basis.centroid, strict=False)) / basis.centroid_norm


@dataclass(frozen=True, slots=True)
class ScoreDecomposition:
    """Full, self-explaining breakdown of one posting's fit_score.

    Phase A1: makes the EXISTING computation legible. ``final`` is the
    authoritative fit_score — ``score_posting`` returns exactly this — so the
    decomposition reconciles to fit_score by construction (no separate math).
    Phase A3: ``applied_corpus_boost`` records the surgical revealed-preference
    boost (and WHY it did/didn't apply); ``final`` includes it.
    """

    scorer_version: str
    weights: dict[str, int]
    sub_scores: dict[str, int | None]
    present: list[str]
    dropped: list[str]
    total_weight: int
    contributions: dict[str, int]
    weighted_mean: float
    score_pre_caps: int
    caps: dict[str, dict[str, Any]]
    applied_corpus_boost: dict[str, Any]
    final: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scorer_version": self.scorer_version,
            "weights": self.weights,
            "sub_scores": self.sub_scores,
            "present": self.present,
            "dropped": self.dropped,
            "total_weight": self.total_weight,
            "contributions": self.contributions,
            "weighted_mean": self.weighted_mean,
            "score_pre_caps": self.score_pre_caps,
            "caps": self.caps,
            "applied_corpus_boost": self.applied_corpus_boost,
            "final": self.final,
        }


def score_posting_decomposed(
    posting: JobPosting,
    profile: OperatorProfile,
    *,
    tier: int | None,
    applied_basis: AppliedBasis | None = None,
) -> ScoreDecomposition:
    """Compute the composite score AND its full decomposition (single source
    of truth). Pure function of (posting, profile, tier, applied_basis).
    Deterministic, no I/O — the corpus-level ``applied_basis`` is injected.

    Phase A1 refactor: this holds the math that ``score_posting`` used to inline.
    Phase A3: a surgical, lift-only applied-corpus boost is applied AFTER the
    caps. Default ``applied_corpus_weight=0`` (or ``applied_basis=None``) => boost
    is 0 => ``final`` byte-identical to the pre-A3 fit_score (no-op). Locked by
    the unchanged-output test matrix.
    """
    parts = score_breakdown(posting, profile, tier=tier)
    # Weighted MEAN over the AVAILABLE weighted features (iterating ``_WEIGHTS``
    # excludes the ``disguised_senior`` flag, a post-composite cap). Only
    # ``semantic_fit`` can be None (row not yet embedded/calibrated): omit it
    # and renormalize over the remaining weights, so a pre-embedding posting
    # scores on the structured features alone — no fake signal — and gains the
    # semantic blend once ``similarity_score`` lands (re-scored on the
    # embedding-sweep tail / profile-save hook).
    sub_scores: dict[str, int | None] = {}
    contributions: dict[str, int] = {}
    present: list[str] = []
    dropped: list[str] = []
    acc = 0.0
    total_weight = 0
    for key, weight in _WEIGHTS.items():
        value = parts[key]
        sub_scores[key] = None if value is None else int(value)
        if value is None:
            dropped.append(key)
            continue
        contributions[key] = int(value) * weight
        acc += contributions[key]
        total_weight += weight
        present.append(key)
    weighted = acc / total_weight if total_weight else 0.0
    score_pre_caps = max(0, min(100, round(weighted)))
    score = score_pre_caps

    # Hard gate (Bestiary 5.21): role_family is a DISQUALIFYING attribute, not
    # a weighted factor. A wrong-role posting (program_management, product_
    # marketing, other) at a Tier-1 company in-geo would otherwise ride the
    # other 75% of weight to a high composite and dominate Best Fit. Cap it at
    # ROLE_GATE_CAP so every genuine PM role outranks it. role_family is NOT
    # NULL on the model (defaults to ``other``), so this is a clean membership
    # test — no NULL case. ``other`` rows mis-bucketed by the ingest regex
    # self-heal: the classifier cron upgrades them to a PM family and re-scores.
    #
    # business_analyst/financial_analyst expansion: three-way instead of
    # binary. ANALYST_FAMILIES rows are acceptable-but-discounted — NOT
    # hard-gated to ROLE_GATE_CAP like a true non-PM family, but capped at
    # ANALYST_GATE_CAP so they can't outrank a genuine PM/PO role riding the
    # same structured score.
    family_value = str(posting.role_family)
    role_gate_fired = (
        family_value not in PREFERRED_FAMILIES and family_value not in ANALYST_FAMILIES
    )
    analyst_gate_fired = family_value in ANALYST_FAMILIES
    if role_gate_fired:
        score = min(score, ROLE_GATE_CAP)
    elif analyst_gate_fired:
        score = min(score, ANALYST_GATE_CAP)

    # Disguised-senior altitude cap (career-changer correction): a PM role
    # under-leveled to pm/unknown but posting a senior USD comp floor is capped
    # at 55 so it can't surface as a top pick. Soft (not excluded) — composes
    # via min() with the gate above. See ``is_disguised_senior``.
    disguised = is_disguised_senior(posting)
    if disguised:
        score = min(score, _DISGUISED_SENIOR_CAP)

    # ── Phase A3: surgical applied-corpus boost (Philosophy 2) ────────────────
    # Lift-only, eligibility-gated, bounded; applied AFTER the caps. ELIGIBLE
    # requires no cap fired AND seniority in-target — so a gated / disguised /
    # senior role gets NO boost (the embedding's blind spots are structurally
    # protected). boost >= 0 and 0 below the reference band => never buries.
    ac_weight = float(getattr(profile, "applied_corpus_weight", 0.0) or 0.0)
    included = list(profile.seniority_levels_included or [])
    sen = str(posting.seniority_level) if posting.seniority_level is not None else None
    # in-target iff a filter is set AND the level is in it (unknown/NULL → False).
    seniority_in_target = bool(included) and sen in included
    # analyst_gate_fired excluded too: the applied-corpus boost must never push
    # an analyst-family row past ANALYST_GATE_CAP any more than the role gate
    # lets a gated row past ROLE_GATE_CAP — same "capped, not boosted" shape.
    eligible = (
        (not role_gate_fired)
        and (not analyst_gate_fired)
        and (not disguised)
        and seniority_in_target
    )

    sim: float | None = None
    applied_fit: int | None = None
    conf: float | None = None
    boost = 0.0
    pre_boost_final = score
    if applied_basis is not None:
        conf = round(_applied_confidence(applied_basis.n), 4)
        if ac_weight > 0 and posting.jd_embedding is not None:
            sim = round(
                _cosine_to_centroid([float(x) for x in posting.jd_embedding], applied_basis), 4
            )
            applied_fit = round(sim * 100)
            denom = _APPLIED_SIM_HI - applied_basis.reference_band
            frac = (
                0.0
                if denom <= 0
                else max(0.0, min(1.0, (sim - applied_basis.reference_band) / denom))
            )
            if eligible:
                boost = ac_weight * _applied_confidence(applied_basis.n) * frac * MAX_APPLIED_BOOST

    if eligible and boost > 0:
        score = int(min(100, round(pre_boost_final + boost)))

    applied_corpus_boost = {
        "weight": ac_weight,
        "n": applied_basis.n if applied_basis is not None else None,
        "confidence_factor": conf,
        "reference_band": applied_basis.reference_band if applied_basis is not None else None,
        "sim": sim,
        "applied_fit": applied_fit,
        "eligible": eligible,
        "eligibility": {
            "role_gate_ok": not role_gate_fired,
            "analyst_gate_ok": not analyst_gate_fired,
            "not_disguised": not disguised,
            "seniority_in_target": seniority_in_target,
            "included_set": included,
            "seniority_level": sen,
        },
        "boost_points": round(boost, 2),
        "pre_boost_final": pre_boost_final,
        "final": score,
    }

    return ScoreDecomposition(
        scorer_version=SCORER_VERSION,
        weights=dict(_WEIGHTS),
        sub_scores=sub_scores,
        present=present,
        dropped=dropped,
        total_weight=total_weight,
        contributions=contributions,
        weighted_mean=weighted,
        score_pre_caps=score_pre_caps,
        caps={
            "role_family_gate": {"fired": role_gate_fired, "cap": ROLE_GATE_CAP},
            "analyst_family_gate": {"fired": analyst_gate_fired, "cap": ANALYST_GATE_CAP},
            "disguised_senior": {"fired": disguised, "cap": _DISGUISED_SENIOR_CAP},
        },
        applied_corpus_boost=applied_corpus_boost,
        final=score,
    )


def score_posting(
    posting: JobPosting,
    profile: OperatorProfile,
    *,
    tier: int | None,
) -> int:
    """Compute the composite 0-100 fit score for a posting.

    Pure function of (posting, profile, tier). Deterministic. No I/O. Returns
    ``score_posting_decomposed(...).final`` — the decomposition is the single
    source of truth; this wrapper preserves the historical int-returning API
    and the mock seam.
    """
    return score_posting_decomposed(posting, profile, tier=tier).final


def bucket_for_score(score: int | None) -> str:
    """Map a fit_score to a coarse bucket label for distribution reporting."""
    if score is None:
        return "unscored"
    if score >= 80:
        return "80-100"
    if score >= 60:
        return "60-79"
    if score >= 40:
        return "40-59"
    if score >= 20:
        return "20-39"
    return "0-19"
