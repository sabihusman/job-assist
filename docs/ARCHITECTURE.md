# Architecture

## System overview

Job Assist is a personal job-search aggregation and triage system. It pulls postings from multiple ATS sources, scores them against a profile and rule set, and surfaces a daily digest of the best matches. It does not apply on the user's behalf.

```
┌──────────────────────────────────────────────────────────────────┐
│                         GitHub Actions Cron                      │
│  daily ingest  →  daily triage  →  daily digest  →  gmail poll  │
└──────────────────┬───────────────────────────────────────────────┘
                   │ HTTP / scheduled
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    apps/api  (Python · FastAPI)                  │
│                                                                  │
│  Adapters  →  Dedupe  →  Triage  →  Digest  →  Gmail  →  RAG    │
│                                                                  │
└──────────────────┬───────────────────────────────────────────────┘
                   │ async SQL
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│             Supabase Postgres + pgvector + Auth                  │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                  apps/web  (Next.js · TypeScript)                │
│        feedback UI  ·  Q&A interface  ·  digest preview          │
└──────────────────────────────────────────────────────────────────┘
```

## Components

### `apps/api` (Python · FastAPI)

Owns all unattended work. Hosted on Railway or Fly.io.

- **Adapters** — One per ATS (Greenhouse, Lever, Ashby, JSearch). Normalize to `JobPosting`.
- **Dedupe** — Hash-based pre-filter + pgvector similarity for fuzzy matches.
- **Triage** — Three layers: (1) hard rules — closed channel, role filter, staffing firm, geo, salary floor, applicant cap (PR #23, see [ADR-008](DECISIONS.md#adr-008--hard-rule-filter--rules-priority-and-defaults)); (2) embedding similarity against profile (Week 3); (3) LLM verdict on top-N only (Week 3-4).
- **Digest** — Daily Resend email with top 8 ranked postings.
- **Gmail** — OAuth, backfill of historical applications, continuous polling for outcomes.
- **RAG** — Self-hosted Q&A over JDs, emails, notes. Replaces NotebookLM.
- **Tailor** — Outreach drafts (Phase 2). Resume tailoring deferred post-Week-6.

### `apps/web` (Next.js · TypeScript)

Owns all human interaction. Hosted on Vercel.

- **Auth** — Supabase Auth, single-user (the operator), magic-link email.
- **Feedback UI** — Four-button triage on each posting (Interested / Not interested / Applied / Snooze).
- **Q&A** — Conversational interface over the operator's job-search history.
- **Digest preview** — View daily digest before sending; manual resend.

### Database (Supabase Postgres + pgvector)

Single project, both apps connect. Core tables:

- `target_company` — operator's priority list with tier and ATS hints
- `job_posting` — canonical posting record
- `posting_source` — per-source variants of a canonical posting
- `ingest_run` — audit log per scheduled pull
- `application_state` — operator's interactions (Interested, Applied, Snoozed)
- `outcome_event` — every email touchpoint per application
- `triage_result` — score + verdict + features per posting
- `embedding_chunk` — pgvector store for RAG

## Data flow (daily)

1. **06:00 UTC** — GitHub Actions triggers `ingest` workflow. Hits API endpoint per source. Adapters fetch, normalize, upsert.
2. **06:30 UTC** — `triage` workflow runs. Hard rules filter → embedding similarity → LLM verdict on top-N.
3. **07:00 UTC** — `digest` workflow generates and sends email via Resend.
4. **Every 15 min, business hours** — `gmail-poll` workflow checks new messages, classifies, updates `application_state`.

## Principles

1. **No automated applying.** The system drafts and triages; the operator applies.
2. **Hard rules before LLM.** Cost discipline. Filter binary fits before spending tokens.
3. **Embeddings primary for dedupe.** Title normalization is unreliable; semantic similarity is robust.
4. **Code handles unattended work. Operator handles judgment.** Anything requiring discretion runs in the web UI.
5. **Every change via PR.** `main` is protected. CI gates merge.

## Deployment

- **Web:** Vercel, preview deploy per PR, production deploy on merge to `main`.
- **API:** Railway (or Fly.io). Production deploy on merge to `main`.
- **DB:** Supabase. Migrations run via Alembic, gated by CI shadow-DB check.

## Current state (2026-06)

This file describes the original plan; the system as built has moved on. Key
deltas (see [`TECH_SPEC.md`](TECH_SPEC.md) for the authoritative current spec):

- **Hosting:** API on Railway; DB on Supabase Postgres + pgvector; web on Vercel.
- **Ingest** is live for Greenhouse/Lever/Ashby (direct) plus Workday/iCIMS via
  the Apify "Fantastic" path (boards that block Railway's egress IP), with broad
  ATS-handle discovery. Daily curated cron + weekly warm-path sweep.
- **Scoring is heuristic + transparent, not learned.** `fit_score` is a six-feature
  weighted composite with a role-family gate and disguised-senior cap. **Version A**
  added, in read-only stages: **A1** the `score_components` decomposition (every
  posting's score is fully explainable), **A2** an applied-corpus (revealed-
  preference) similarity signal, and **A3** a bounded, lift-only **surgical boost**
  of that signal behind a default-0 weight. Semantic cosine also feeds an optional
  `best_fit_semantic` sort.
- **Outcome-driven Pipeline:** Applied/Passed/Rejected/Pipeline views are built
  from Gmail `outcome_event`s (most still unlinked to a posting), unified with
  manual `posting_action` / `application_state`. "Reinstate" returns a passed role
  to triage by appending a `reset` action (append-only).
- **Cron chain (UTC):** ingest 06:00 → broad-ingest 06:30 → company-enrich 07:00 →
  classifier 07:30 → divisions 08:00 → JD-summaries 08:30 → embeddings 09:00 →
  ingest-health 09:30; Gmail poll every 6h.
- **Ops without a prod DB:** a suite of read-only `/admin/diagnostics/*` endpoints
  (each with a manual probe workflow) answers corpus questions; manual
  `workflow_dispatch` workflows carry the auth token for prod reads/writes.
- **Digest / RAG Q&A** from the original plan are not built; triage + the
  diagnostics surface are the live product.
