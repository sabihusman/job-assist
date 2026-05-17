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
