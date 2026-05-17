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
| `applicant_cap` | `150` |
| `geo_whitelist` | `Remote`, `Des Moines`, `NYC`, `New York`, `Austin`, `San Francisco`, `Bay Area`, `Seattle`, `Minneapolis`, `Chicago` |
| `staffing_firm_blocklist` | Robert Half, Aerotek, Insight Global, Apex Systems, Beacon Hill, TEKsystems, Modis, Randstad, Kforce, Adecco |

**Rationale (priority order):**
- `closed_channel` and `role_filter` are operator-set company-level signals; cheapest checks, run first.
- `staffing_firm` is a substring test that doesn't require any posting-content parsing.
- `geo_whitelist` runs before `salary_floor` because most postings have a location but only some have parseable comp.
- `salary_floor` and `applicant_cap` both **tolerate unknown values** — they only fire when the data is present and unambiguous, so a missing field never becomes a false negative.

**Schema deviation from the PR #23 spec:** the spec sketched `apply_hard_rules` reading `target_company.is_closed_channel` directly. Closed-channel state already lives in its own table (`closed_channel`) with a `unsealed_at IS NULL` semantic for "currently sealed", so denormalising it onto `target_company` would create two sources of truth that inevitably drift. Instead, `apply_hard_rules` takes the already-fetched `ClosedChannel | None` as a parameter — the future triage cron does the query.

**Migration path:** thresholds will move to an `operator_profile` table in PR #29 so the operator can tune them from the web UI without redeploying. The current dataclass becomes the seed values for that row.
