# Roadmap

Six-week build, intentionally phased. Each phase has a clear "useful" milestone.

## Week 1-2 — Skateboard

**Goal:** prove the pipeline. Get clean structured JD data flowing in. Get historical Gmail data labeled.

Build:
- `target_company`, `source_map`, `job_posting`, `posting_source`, `ingest_run`, `application_state`, `outcome_event` tables
- Greenhouse, Lever, Ashby adapters
- Hard-rule filter (geo, [Company A]-non-PM, banned-verb scan in JD, ≥3-rejection company auto-flag)
- Deterministic scoring (no LLM yet)
- Gmail OAuth + backfill script + LLM classifier (Gemini Flash, free tier)
- Continuous Gmail polling (15-min cadence)
- Manual review via Supabase table view
- GitHub Actions cron, daily

Do not build: Workday, NotebookLM export, feedback UI, tailoring, embeddings, LLM verdicts.

## Week 3-4 — Bicycle

**Goal:** the daily digest becomes useful. Saving time starts.

Build:
- Embedding scoring with pgvector (Gemini embeddings, free tier)
- Embedding-based dedupe (with hash pre-filter)
- LLM one-line verdicts (Gemini Flash Lite, top-N only)
- Daily digest via Resend (top 8, priority flags for rejection-pattern companies)
- Streamlit-free Next.js feedback UI (Interested / Not interested / Applied / Snooze)
- Calibration report showing which features drove each score
- Snooze logic

Do not build: learned scoring weights, JSearch, tailoring, RAG Q&A.

## Week 5-6 — Car

**Goal:** Phase 2 capabilities. System is now a real product.

Build:
- Cross-source dedupe tuning (threshold calibration based on real data)
- JSearch adapter (free tier or pay-as-you-go) if direct ATS coverage has gaps
- Outreach tailoring agent (Claude Sonnet API, <300-char rules)
- Self-hosted RAG Q&A system (retrieval + synthesis + Streamlit Q&A tab in Next.js)
- Eval harness (10 reference Q&A pairs, regression tracking)
- LLM extraction of structured fields (salary, seniority, role_family, remote_type, locations_normalized)

Do not build: resume tailoring, Workday adapter, learned scoring.

## Post-Week-6 — separate scoped projects

- Resume tailoring agent (full locked rules — its own project, complexity warrants it)
- Workday CXS adapter (own scoped project, possibly Playwright-based)
- Learned scoring weights (after 50-100 labeled feedback decisions)
- Portfolio writeup (DECISIONS.md → blog post, eval framework writeup)
- Source-yield analytics (which sources convert to interviews)

## Success metrics (Week 6)

- Time saved per week: target 5+ hours
- Application velocity: maintain or increase rate of *quality* applications
- Source yield: answerable with data
- Triage quality: precision/recall against manual decisions, tracked over time

---

## Status (2026-06)

What actually shipped vs the six-week plan:

**Built and live:** Greenhouse/Lever/Ashby adapters + Workday/iCIMS via Apify;
broad ATS-handle discovery; hard-rule filter (geo incl. US-remote, seniority,
PM/PO gate, staffing, salary); heuristic `fit_score` with a transparent
decomposition; pgvector embeddings + calibrated semantic similarity; Gmail
OAuth + backfill + 6-hourly polling + outcome classification; the Next.js triage
UI (keyboard-driven) plus Applied/Passed/Rejected/Pipeline/Companies/Contacts/
Resumes/Stats/Settings; per-application résumé attach + text extraction; health
dot + Sentry; full GitHub-Actions cron chain.

**Beyond the plan — Version A (revealed-preference scoring), shipped read-only
first:** A1 score-decomposition surface → A2 applied-corpus similarity signal →
A3 a bounded, lift-only surgical boost (default-0, dormant until tuned). Plus a
large read-only `/admin/diagnostics/*` suite + manual ops workflows for prod
introspection without a DB connection, and "Reinstate" (passed → triage via an
appended `reset`).

**Not built (deliberately deferred):** daily Resend digest, self-hosted RAG Q&A,
outreach/résumé tailoring agents, JSearch adapter, learned scoring weights. The
triage queue + diagnostics surface — not a digest email — became the product.
