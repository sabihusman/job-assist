# Architecture Decision Records

Each ADR captures a decision, its context, and the alternatives considered. Decisions made during scoping are written up here for the portfolio.

---

## ADR-001 · No automated applying

**Status:** Accepted

**Context:** A natural extension of a job-search aggregator is to apply on the operator's behalf — clicking through LinkedIn Easy Apply, filling Workday forms, etc. This is the loudest feature request in this product category.

**Decision:** The system never applies automatically. It surfaces, ranks, and drafts. The operator always clicks submit.

**Rationale:**
1. LinkedIn's User Agreement prohibits automation; bans are common, and a banned LinkedIn account is more damaging to a job search than any time saved.
2. Companies increasingly detect AI-generated applications; mass-apply tools produce worse outcomes than fewer, careful applications.
3. The operator already has a likely ATS auto-reject pattern at one target (3+ rejections); compounding that with high-volume automation could replicate the problem at every target.

**Alternatives considered:**
- *Full automation* — rejected on ToS and quality grounds.
- *Assisted apply (browser extension that pre-fills your logged-in session)* — kept as a future option but explicitly out of scope for Phase 1-2.

---

## ADR-002 · Hybrid Python API + Next.js web

**Status:** Accepted

**Context:** Two viable stacks: pure TypeScript (mirroring Easterseals' Next.js + Vitest pipeline) or pure Python (matching Juno's FastAPI + Postgres setup). The question is which produces a better app.

**Decision:** Hybrid. Python FastAPI backend for ingestion / triage / LLMs / Gmail / RAG. Next.js frontend for auth / feedback UI / Q&A. Single monorepo, two deployments.

**Rationale:**
1. Python ecosystem is materially deeper for LLM, embedding, and RAG work — the core of the product.
2. SQLAlchemy + Alembic + pgvector-python are more mature than any TypeScript equivalent for this data shape.
3. Next.js is materially better than Streamlit for the operator-facing UI. Mobile-friendly, polished, real product.
4. Service-oriented split is a more honest portfolio artifact than picking one stack ideologically.
5. ~6 hours of extra scaffolding cost; no ongoing complexity.

**Alternatives considered:**
- *Pure TypeScript* — would reuse Easterseals workflow exactly, but the LLM/embedding work is less natural in Node, and the Python ecosystem advantage is meaningful for the core product.
- *Pure Python + Streamlit* — fastest to ship but Streamlit's UI quality is a real downgrade and weak as a portfolio artifact.

---

## ADR-003 · Self-hosted RAG Q&A instead of NotebookLM

**Status:** Accepted

**Context:** The system needs a layer for the operator to ask analytical questions over their job-search history ("what did this week's rejections have in common?"). NotebookLM Pro is an obvious off-the-shelf option.

**Decision:** Build a self-hosted RAG Q&A system in Week 5-6. Skip NotebookLM.

**Rationale:**
1. NotebookLM does not auto-sync from Google Drive; manual re-sync is required for fresh data. This kills the "wake up to current analytics" workflow.
2. NotebookLM can only reason over unstructured text. The operator's data has rich structure (application_state, outcome_event, triage_result). Self-hosted can join structured queries with unstructured retrieval.
3. The infrastructure (pgvector, embeddings, Gemini Flash) already exists in the system for triage; RAG reuses it.
4. Self-hosted RAG with documented eval framework is a stronger portfolio artifact than "I use NotebookLM."
5. Build cost: ~20 hours in Week 5-6. Marginal LLM cost: $1-3/mo.

**Alternatives considered:**
- *NotebookLM Pro fully in scope* — sync friction kills daily workflow.
- *Weekly export + manual re-sync* — works but adds a manual step; loses cross-source structured reasoning.

---

## ADR-004 · Hard rules before LLM scoring

**Status:** Accepted

**Context:** Triage involves three signals: hard rules (geo, banned-verb scan, ≥3-rejection company flag), embedding similarity to profile, and LLM-generated verdict. These can run in any order.

**Decision:** Hard rules run first as a boolean filter. Embedding similarity and LLM verdicts run only on postings that pass hard rules.

**Rationale:**
1. Cost discipline. LLM verdicts on 200 postings/day cost more than verdicts on 20.
2. Debuggability. When a posting is filtered, the reason is one of a small set of explicit rules, not a vector score.
3. Predictability. Hard exclusions (e.g., Bay Area outside year-2 window) shouldn't be overrideable by embedding similarity.

---

## ADR-005 · Continuous Gmail monitoring (not just one-time backfill)

**Status:** Accepted

**Context:** Outcome data (rejections, interview invites) lives in Gmail. Two options: one-time backfill of historical mail, or backfill plus continuous monitoring.

**Decision:** Both. Backfill in Week 1-2 to label 100+ historical applications. Continuous polling (15-min cadence during business hours) keeps `application_state` current going forward.

**Rationale:**
1. Marginal effort is small once OAuth and classifier exist.
2. Manual outcome entry has a near-100% miss rate after a few weeks of search fatigue.
3. Fresh outcome data is needed for the company-pattern flag (companies with ≥3 rejections trigger special handling).

---

## ADR-006 · Tier-based ranking instead of single binary filter

**Status:** Accepted

**Context:** Operator stated "I'll take any PM role to get my foot in the door." This sounds like maximum flexibility but functionally removes the target function the triage system needs.

**Decision:** Four-tier ranking, with hard rules acting as the only true exclusion.

- **Tier 1** — Wealthtech / fintech where FS domain is leverage AND product is technology (Q2, iCapital, MeridianLink, Addepar, Plaid, Stripe, Mercury, Brex, Carta, Ramp, Pearl Health, Bullhorn).
- **Tier 2** — Pure tech (high upside, lower probability) — Stripe, Notion, Linear, Anthropic, Figma, Atlassian, etc.
- **Tier 3** — FS PM at banks / wealth / insurance carriers where operator has applied successfully before.
- **Tier 4** — Any other legitimate PM role.

**Rationale:**
1. Operator's strongest candidacy is wealthtech/fintech — the bridge between FS background and tech-PM goal.
2. Surfacing everything that passes hard rules preserves optionality; ranking tells the operator where to spend time.
3. Tier anchors calibrate embedding similarity for "this company is like the ones I want" without requiring exhaustive enumeration.

---

## ADR-007 · Fresh Supabase project (not extending Juno)

**Status:** Accepted

**Context:** Operator has an existing Supabase project for Juno PM. Could add a `jobsearch` schema there, or create a fresh project.

**Decision:** Fresh Supabase project.

**Rationale:**
1. Portfolio cleanliness — can be shared / demoed without exposing Juno.
2. Resource isolation — schema migrations don't risk Juno data.
3. Free tier covers both projects; no cost penalty.

---

## ADR-008 · Hard-rule filter — rules, priority, and defaults

**Status:** Accepted (PR #23)

**Context:** ADR-004 established that hard rules run before any LLM or embedding scoring. This ADR fixes the *specific* rules, their priority order, and the default thresholds that ship in PR #23.

**Decision:** Six rules, evaluated in this priority order. The first to fail short-circuits the chain:

1. **closed_channel** — operator has flagged this company as off-limits.
2. **role_filter** — company has `role_filter='non_pm_only'` and posting `role_family ∈ {product_management, product_owner}`.
3. **staffing_firm** — canonical company name (or target_company name) matches the case-insensitive substring blocklist.
4. **geo_whitelist** — the posting's `location_raw` plus every `locations_normalized[*]` city/region fails to intersect the whitelist.
5. **salary_floor** — `salary_max` is known *and* `salary_period=annual` *and* currency is USD (or unset) *and* below the floor.
6. **applicant_cap** — `applicant_count` is known and exceeds the cap.

Defaults (the values that seed `HardRuleConfig`):

| Threshold | Default |
|---|---|
| `salary_floor_usd` | `85_000` |
| `applicant_cap` | `500` |
| `geo_whitelist` | `Remote`, `Des Moines`, `NYC`, `New York`, `Austin`, `San Francisco`, `Bay Area`, `Seattle`, `Minneapolis`, `Chicago` |
| `staffing_firm_blocklist` | Robert Half, Aerotek, Insight Global, Apex Systems, Beacon Hill, TEKsystems, Modis, Randstad, Kforce, Adecco |

**History note (`applicant_cap`):** Raised from 150 → 500 in May 2026 ahead of the LinkedIn adapter. Competitive enterprise PM roles on LinkedIn regularly surface 200–800 applicant counts, so the original 150 would have triggered a near-universal drop on day one of LinkedIn ingestion. The migration (`a1b2c3d4e5f6`) updates the column server default and the seeded singleton row (only if still on the old default — operator-customized values are preserved). The Settings slider's `max` was widened 500 → 1000 in the same PR to give 2× headroom for operator tuning.

**Rationale (priority order):**
- `closed_channel` and `role_filter` are operator-set company-level signals; cheapest checks, run first.
- `staffing_firm` is a substring test that doesn't require any posting-content parsing.
- `geo_whitelist` runs before `salary_floor` because most postings have a location but only some have parseable comp.
- `salary_floor` and `applicant_cap` both **tolerate unknown values** — they only fire when the data is present and unambiguous, so a missing field never becomes a false negative.

**Schema deviation from the PR #23 spec:** the spec sketched `apply_hard_rules` reading `target_company.is_closed_channel` directly. Closed-channel state already lives in its own table (`closed_channel`) with a `unsealed_at IS NULL` semantic for "currently sealed", so denormalising it onto `target_company` would create two sources of truth that inevitably drift. Instead, `apply_hard_rules` takes the already-fetched `ClosedChannel | None` as a parameter — the future triage cron does the query.

**Migration path:** thresholds will move to an `operator_profile` table in PR #29 so the operator can tune them from the web UI without redeploying. The current dataclass becomes the seed values for that row.

---

## ADR-009 · Gmail poll watermark derives from data, not a state row

**Status:** Accepted (PR #25)

**Context:** Continuous Gmail polling needs a watermark — "what's the most recent email I've already classified?" — to scope each 15-minute query. Two ways to store that watermark:

| | Option A — derived from data | Option B — explicit state row |
|---|---|---|
| Source of truth | `MAX(outcome_event.received_at)` | new `poll_watermark` row updated on each successful poll |
| New schema | none | one new column or table |
| Drift risk | none (data IS the watermark) | the state row and the actual data can diverge if a run crashes between insert and update |
| Recovery after crash | automatic | requires manual reconciliation |

**Decision:** Option A. The poll endpoint runs `SELECT MAX(received_at) FROM outcome_event` on every call; if the table is empty (fresh deploy, no backfill yet), it falls back to `now() - 24h`.

**Rationale:**
1. **Drift-resistant.** The watermark IS the data. A partial commit followed by a crash leaves the watermark exactly at the last-actually-inserted row's `received_at`. The next poll naturally retries everything from there forward without manual intervention.
2. **No new migration.** One less schema artefact to maintain across environments.
3. **Easy to reason about.** "What's the most recent email I know about?" is a self-explaining query that matches the operator's mental model.

**Trade-off:** the `MAX` query runs on every poll. Cheap — the `idx_outcome_event_received_at` index makes it O(log n), and at 96 polls/day × < 1 ms each, it's lost in the noise.

**Bootstrap fallback (24 hours):** the orchestrator decides this. The default isn't load-bearing — a fresh deploy that immediately gets the production backfill (PR #22) will have weeks of `outcome_event` rows by the time the first 15-min poll fires.

---

## ADR-010 · Scoring evolution shipped read-only first (Version A staging)

**Status:** Accepted

**Context:** Adding a revealed-preference (applied-corpus) signal to `fit_score`
risked silently changing every score before anyone could see whether the signal
was sane.

**Decision:** Ship in three read-only-first stages. **A1** — make the existing
`fit_score` legible via a stored `score_components` decomposition (no value
change). **A2** — compute the applied-corpus similarity as a pure read-only
diagnostic (compute + expose, never blend). **A3** — blend it behind a default-0
weight so deploy is a no-op until the operator opts in.

**Rationale:** each stage was independently inspectable and reversible; the
decomposition (A1) became the surface A3's contribution is shown in; A2 proved
the signal coherent (and surfaced its blind spot) before it could move a score.

**Alternatives considered:** blend directly behind a low default weight — rejected;
no way to look before it influences anything, and the decomposition surface
wouldn't exist to audit it.

---

## ADR-011 · Applied-corpus boost is surgical (lift-only), not a weighted blend

**Status:** Accepted

**Context:** The applied-corpus embedding captures topical similarity but is blind
to seniority and the operator's negative/exclude preferences — it ranks senior/
staff PM roles highly even though the heuristic correctly caps them.

**Decision:** Philosophy 2 — the boost can only **lift** a posting, and only when
no cap fired AND seniority is in-target (`eligible = role-gate-ok AND
not-disguised AND seniority_in_target`). Applied after the caps, bounded by
`weight × f(n) × ramp × MAX_BOOST`, and 0 below the corpus reference band.

**Rationale:** a plain weighted-mean blend (Philosophy 1) inherently nudges senior
roles up and shaves high-fit/low-sim roles down — exactly the embedding's blind
spots. Boost-only + eligibility turns the heuristic's seniority/negative signals
into a hard structural guard: it can't lift gated/capped/senior roles and can't
bury anything. Confidence `f(n)=min(1,n/30)` keeps it weak while the applied
corpus is thin (n≈16).

**Alternatives considered:** Philosophy 1 (7th weighted sub-score) — rejected on
the blind-spot and no-bury grounds above, evaluated side-by-side on real
divergence cases before choosing.

---

## ADR-012 · Reinstate a passed role by appending, not mutating

**Status:** Accepted

**Context:** "Reinstate" returns a passed role to the triage queue. `posting_action`
is append-only and feeds `resolved_status`.

**Decision:** Append a new `reset` action rather than deleting/editing the original
`not_interested` row. `reset` is already triage-eligible, so the latest-action
membership flips the posting back to triage; the audit trail reads
"not_interested (reason) → reset".

**Rationale:** preserves the append-only history and the firewall/resolved-status
logic; needs no new action_type, endpoint, or response-shape change. The same
`reset` primitive already powers bulk-undo.
