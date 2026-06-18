# Job Assist — Technical Specification

_Last updated: 2026-06-10 · Reflects `main` through #184 (Gmail sweep health check)._

A single-operator job-search aggregation and triage system. It crawls ATS job
boards, enriches and scores postings with an LLM, surfaces a keyboard-driven
triage queue, and tracks the application pipeline from Gmail outcome emails.

This document is the consolidated technical spec. Companion docs go deeper on
specific facets: [`ARCHITECTURE.md`](ARCHITECTURE.md) (system narrative),
[`architecture/job-assist-architecture.md`](architecture/job-assist-architecture.md)
(code-traced map), [`DESIGN_SYSTEM.md`](DESIGN_SYSTEM.md), [`DECISIONS.md`](DECISIONS.md),
[`BESTIARY.md`](BESTIARY.md) (notable bugs), [`RUNBOOK.md`](RUNBOOK.md), and
[`ROADMAP.md`](ROADMAP.md). The UI contract lives in the root
[`job-assist-ui-spec.md`](../job-assist-ui-spec.md).

---

## 1. Overview

| | |
|---|---|
| **Users** | Single operator (personal tool, dev-mode auth). |
| **Core loop** | Ingest → enrich (LLM) → score → **triage** → apply → track outcomes. |
| **Repo** | pnpm monorepo: `apps/web` (Next.js) + `apps/api` (FastAPI, Python). |
| **Hosting** | API on **Railway** (Postgres + pgvector), web on **Vercel**, scheduled jobs on **GitHub Actions**. |
| **LLM** | Google **Gemini** (classification, embeddings, JD summaries, Gmail outcome classification). |

`apps/api` is intentionally **not** a pnpm workspace member (it is Python);
`pnpm-workspace.yaml` lists `apps/web` explicitly.

---

## 2. Architecture

```
GitHub Actions crons ─┐
                      ▼
            ┌──────────────────────┐      ┌─────────────────────┐
  ATS  ───▶ │  FastAPI (Railway)   │ ───▶ │ Postgres + pgvector  │
 boards     │  ingest · enrich ·   │      │  (Railway)           │
 Gmail ───▶ │  score · triage API  │ ◀─── │                      │
 Gemini ◀──▶│                      │      └─────────────────────┘
            └──────────▲───────────┘
                       │ same-origin proxy (injects bearer token)
            ┌──────────┴───────────┐
  Browser ─▶│  Next.js (Vercel)    │
            │  App Router + RQ     │
            └──────────────────────┘
```

- **Frontend never calls the API directly.** A Next.js route handler proxies
  `/api/*` to the FastAPI origin and injects the `API_AUTH_TOKEN` server-side, so
  the bearer token never reaches the browser.
- **Scheduled work is cron-driven**, not in-process: each GitHub Actions workflow
  `curl`s an `/admin/*` endpoint with the bearer token.

---

## 3. Tech stack

**Backend** (`apps/api`, Python ≥ 3.12)
- FastAPI ≥ 0.115, Pydantic v2 + pydantic-settings
- SQLAlchemy 2.0 (async) + Alembic, asyncpg, **pgvector** 0.3
- `google-genai` (Gemini), httpx, sentry-sdk
- Tooling: **ruff** (lint+format), **mypy** (strict), **pytest** (+ asyncio, DB-gated)

**Frontend** (`apps/web`)
- Next.js 15 (App Router) + React 19, TypeScript 5.7
- TanStack React Query 5, Tailwind CSS 3.4, shadcn-style UI primitives
- Tooling: **biome** (lint), **tsc**, **vitest** (unit), **Playwright** (E2E vs Vercel preview)
- `openapi-typescript` generates `src/lib/types/openapi.ts` from `apps/api/openapi.json`

---

## 4. Data model

PostgreSQL. ORM models in `apps/api/src/job_assist/db/models/`. Schema evolves via
Alembic migrations (`apps/api/migrations/versions/`).

| Entity | Purpose |
|---|---|
| **job_posting** | Canonical posting: company/title/role family/seniority/location/salary, `fit_score`, `score_components` (JSONB — the Phase-A1 fit_score decomposition), `jd_embedding` (Vector(768)), `similarity_score` (calibrated 0–100), `classified_at`/`embedded_at`, enrichment fields, `closed_at`. |
| **target_company** | Companies of interest; tier, ATS handle, `source` (curated/manual), domain. |
| **division** | Department/team sub-unit of a company (enriched). |
| **posting_source** | Per-posting source/ATS provenance. |
| **discovered_handle** | ATS handles found by broad discovery; `last_ingested_at` drives broad-fresh health. |
| **ingest_run** | Audit log per ingestion run (source, started/finished, status, counts). |
| **posting_action** | Append-only operator actions on a posting (interested / not_interested / applied / snoozed / reset). Latest row = current state. Carries a pass `reason`. |
| **application_state** | Per-posting manual lifecycle status (applied → interview → offer → accepted → rejected). |
| **application_resume** / **resume_version** | Résumé attached to an application; versioned résumé variants. |
| **outcome_event** | Classified Gmail message (application_confirmation, interview invites, rejections…). Mostly **unlinked** (`target_company_id`/`job_posting_id` NULL). |
| **gmail_sweep_run** | Audit log per Gmail poll/backfill sweep (kind, started/finished, status, counts) — powers the Gmail health check + last-sweep runtime. |
| **contact** / **outreach_message** | Networking contacts and outreach log. |
| **closed_channel** | Records a company/role channel the operator closed, with reason. |
| **operator_profile** | Singleton (id=1): geo whitelist, seniority bands, `similarity_weight` (semantic sort blend) + `applied_corpus_weight` (Phase-A3 revealed-preference boost), `looking_for_text`/`looking_for_embedding` — drives hard-rule filtering and scoring. |
| **triage_result** | Cached hard-rule/scoring evaluation per posting (dormant — the live decomposition lives on `job_posting.score_components`). |

### Key invariants
- **No fan-out:** `outcome_event` rows are linked to a specific posting **only** by
  the #162 role matcher (`job_posting_id`), never by company. Company-level signals
  are informational hints, never tab membership.
- **Pass-reason vocabulary** is guarded by a `posting_action.reason` CHECK
  constraint (Python `ActionReason` enum is canonical). The `too_many_open_apps`
  reason is a portfolio pass — **excluded** from calibration fit-learning
  aggregates so it never feeds the scorer.

---

## 5. Backend subsystems

### 5.1 Ingestion
- **Adapters** (`adapters/`): Greenhouse, Lever, Ashby, Workday, iCIMS, plus
  Fantastic.jobs/Apify for boards that block Railway's egress IP. A common
  `normalization` + `title_filter` layer maps raw postings to `job_posting`.
- **Curated ingest** (`ingestion.py`) pulls known company handles daily.
- **Broad ingest** (`broad_ingest.py`, `cdx_discovery.py`) discovers new ATS
  handles, trial-ingests them, and records `discovered_handle`.
- Concurrency-safe sweeps use `SELECT … FOR UPDATE SKIP LOCKED` (`sweep_claim.py`)
  so overlapping cron runs don't double-call Gemini.

### 5.2 LLM enrichment (Gemini)
- **Classifier** (`classifier.py`): role family / seniority / hard-rule signals →
  stamps `classified_at`.
- **Embeddings** (`embeddings.py`): `gemini-embedding-001` @ 768 dims into
  `jd_embedding`; per-row retry with attempt cap; exhausted errors are the
  queryable proxy for "LLM calls failing".
- **Company / division / JD-summary enrichment**: one-sentence descriptions and
  operator-facing JD summaries (`jd_summary_markdown`), each with its own model +
  attempt-cap settings.

### 5.3 Scoring & ranking
- `scoring.py` computes a heuristic `fit_score` (0–100) as a weighted mean of six
  sub-scores (role_family 20, seniority 20, salary 15, tier 10, geo 15,
  semantic_fit 20), renormalized when `semantic_fit` is NULL, then a role-family
  hard gate (cap 40) and a disguised-senior cap (55).
- **Score decomposition (Phase A1):** `score_posting_decomposed()` is the single
  source of truth — `score_posting()` returns `.final` off it. It emits
  `job_posting.score_components` (JSONB): every sub-score, weight, contribution,
  renormalization (present/dropped), `score_pre_caps`, which caps fired, and
  `final`. `final == fit_score` by construction; surfaced read-only via
  `/admin/diagnostics/score-decomposition`.
- **Applied-corpus boost (Phase A3, Philosophy 2):** a bounded, lift-only,
  eligibility-gated revealed-preference boost behind
  `operator_profile.applied_corpus_weight` (**default 0 = no-op**). Cosine of the
  posting to the centroid of the applied (`resolved_status='applied'` AND not
  role-gated) embeddings; `boost = weight × f(n) × clamp((sim−reference_band)/
  (0.92−reference_band),0,1) × 10`, `f(n)=min(1,n/30)`. Applied AFTER the caps;
  **eligible = role-gate-ok AND not-disguised AND seniority-in-target**, so it
  structurally cannot lift gated/capped/senior roles, and (lift-only, 0 below the
  band) never buries. Recorded in `score_components.applied_corpus_boost` with the
  full eligibility breakdown. Basis loaded once per sweep (`applied_corpus.py`).
- `best_fit_semantic` sort blends `fit_score` with calibrated cosine similarity
  behind `operator_profile.similarity_weight` (0 = off) — a separate *sort* knob
  from the A3 *score* boost.
- `triage/hard_rules.py` applies reversible hard rules (geo whitelist, seniority
  bands, PM/PO gate) built from the operator profile. The geo gate passes
  US/unspecified-remote (`_remote_kind`) while still failing region-qualified
  non-US remote.

### 5.4 Gmail outcomes pipeline
- OAuth via stored refresh token; `gmail/backfill.py` runs `run_backfill` (wide
  window) and `run_poll` (since `MAX(outcome_event.received_at)` — no separate
  watermark table). Each message is classified by Gemini into an outcome type.
- `/admin/gmail/poll` + `/backfill` wrap their work in `record_sweep`
  (`services/gmail_sweep_run.py`), which persists timing from an **isolated DB
  session** so the record survives even if the sweep's own transaction rolls back.
- `outcome_posting_match.py` / `outcome_relink.py` link outcomes to a posting
  (by role) or to a `target_company` (by domain/name), best-effort.

### 5.5 Pipeline / Applied / Rejected
- Outcome events are bucketed client-side by Gmail thread (latest-stage-wins).
  Company labels come from a name-extraction chain (`companyFromSubject`).
- `unifyApplied()` / the Rejected tab merge manual application state with
  Gmail-detected outcomes, deduped by `posting:<id>`, source-tagged.

### 5.6 Company-level application awareness
- `company_signals.py` computes per-company `{active_applications, rejections}`
  from outcome history, matched on **normalized company name** (capturing the
  unlinked majority), with a token-subset **ambiguity guard** (show nothing rather
  than a wrong count). Surfaced as triage badges (amber at ≥3 active apps).

---

## 6. API surface

FastAPI, `dict[str, Any]` responses (wire types pinned in
`apps/web/src/lib/triage/types.ts`). All routes except `/health` sit behind the
bearer-token middleware. Grouped:

- **Public/triage:** `/postings`, `/postings/{id}`, `/postings/{id}/state`,
  `/postings/bulk-state`, `/postings/{id}/status`, `/postings/{id}/resume`,
  `/postings/export.xlsx`, `/companies`, `/companies/repeat-signals`, `/outcomes`,
  `/contacts*`, `/outreach/recent`, `/operator/profile`, `/resume-versions`.
- **Stats:** `/stats/calibration`, `/stats/funnel`, `/stats/ingest`.
- **Admin / ops:** `/admin/ingest/*`, `/admin/broad-ingest/run`,
  `/admin/discover-ats/run`, `/admin/gmail/{poll,backfill}`, `/admin/outcomes/*`
  (incl. `relink`), `/admin/score/*`, `/admin/embeddings/*`, `/enrichment/*`,
  `/admin/seed/*`, `/admin/companies/crawl-config`,
  `/admin/postings/{reeval-hard-rules,backfill-score-components,reparse-salary}`,
  `/admin/ingest/health`, `/admin/cron-status`, `/admin/auth-status`.
- **Read-only diagnostics** (`/admin/diagnostics/*`, all pure SELECT, each with a
  manual `*-probe` workflow): `outcome-linking`, `no-candidate-breakdown`,
  `rag-corpus`, `triples`, `curated-zero-postings`, `company-references`,
  `ingest-scoring`, `high-fit-gaps`, `semantic-readiness`, `paused-companies`,
  `applied-corpus-embeddings`, `applied-similarity`, `score-decomposition`.
- **Reinstate a passed role** reuses `POST /postings/{id}/state` with
  `action_type='reset'` (append-only; no dedicated endpoint).

`apps/api/openapi.json` is a committed snapshot; CI fails on drift from
`app.openapi()`.

---

## 7. Frontend

App Router pages (`apps/web/src/app/`): Triage (`/`), Applied, Passed, Rejected,
Pipeline, Companies, Contacts, Resumes, Stats, Settings.

- **Triage** is the core surface: keyboard-driven (J/K nav, 1–4 actions, `2`→
  reason picker), score-forward cards, multi-select bulk actions, company-app
  badges, ⌘K command palette.
- **Data:** React Query against the same-origin proxy; one cached fetch per
  cross-cutting dataset (e.g. company signals) passed down to pure leaf
  components to avoid coupling them to React Query.
- **System health dot** (`HealthDot`) polls `/admin/ingest/health` every 60s;
  green/amber/red with a per-check popover (ingest, broad-fresh, starvation, LLM,
  Gmail sweep + runtime). A fetch error reads red — a dead backend never shows green.

---

## 8. Scheduled jobs (GitHub Actions, UTC)

| Workflow | Schedule | Hits |
|---|---|---|
| `ingest-daily` | 06:00 | curated ingest |
| `broad-ingest` | 06:30 | handle discovery + trial ingest |
| `enrich-companies` | 07:00 | company descriptions |
| `enrich-classifier` | 07:30 | Gemini classifier sweep |
| `enrich-divisions` | 08:00 | division descriptions |
| `cron-health` | 08:00 | internal health roll-up |
| `enrich-jd-summaries` | 08:30 | JD summaries |
| `embed-postings` | 09:00 | embeddings sweep |
| `ingest-health` | 09:30 | curls `/admin/ingest/health`, alerts on `ok=false` |
| `gmail-poll` | every 6h | `/admin/gmail/poll` |
| `keepalive` | every 5m | pings `/health` (Railway warm) |

Plus **manual `workflow_dispatch` ops/diagnostic workflows** (not scheduled):
`crawl-config`, `ingest-handle`, `reeval-hard-rules`, `outcome-relink`,
`backfill-score-components`, `deactivate-company`, and the read-only `*-probe`
workflows for each `/admin/diagnostics/*` endpoint. These carry the
`API_AUTH_TOKEN`/`API_URL` secrets and are the operator's lever for prod reads
and writes without a direct DB connection.

---

## 9. Health & observability

`GET /admin/ingest/health` is a dead-man's-switch verdict (`ok` + three-state
`severity`). Checks:

| Check | Meaning | Severity if failing |
|---|---|---|
| `recent_success` | a successful `ingest_run` within ~26h | **down** (red) |
| `no_hard_failures` | zero failed runs in window | **down** (red) |
| `broad_fresh` | a handle swept within ~26h | degraded (yellow) |
| `not_starved` | ≥1 net-new posting in 3 days | degraded |
| `llm_healthy` | classifier ran <24h ago, no error pile-up | degraded (hard if severe) |
| `gmail_healthy` | a Gmail sweep started <13h ago and last didn't fail | degraded |

Metrics carry timestamps + the **last Gmail sweep runtime**. Sentry captures
backend errors.

---

## 10. Security & auth

- Single shared **bearer token** (`api_auth_token`) gates every route except
  `/health`. Rollout via `auth_enforce`: warn-only (log, allow) → enforce (401).
- Token is injected **server-side** by the Next.js proxy and by crons via the
  `API_AUTH_TOKEN` secret — never exposed to the browser, never `NEXT_PUBLIC_*`.
- Third-party tokens (Apify, Gemini, Gmail refresh token, logo.dev) are
  server-side env only. This is a single-user dev-mode deployment; per-route
  authz/RBAC is out of scope.

---

## 11. Configuration (env)

Loaded by `config.py` (`Settings`, pydantic-settings; `.env` locally, Railway env
+ GitHub secrets in CI). Key vars:

- **Core:** `DATABASE_URL`, `ENVIRONMENT`, `API_AUTH_TOKEN`, `AUTH_ENFORCE`, `CORS_ORIGINS`.
- **LLM:** `GEMINI_API_KEY` (+ per-task model ids: classifier/company/division/JD/embedding), `EMBEDDING_DIM=768`, attempt caps.
- **Gmail:** `GMAIL_CREDENTIALS_JSON`, `GMAIL_REFRESH_TOKEN` (missing → endpoints 503 with a clear hint).
- **Ingest:** `APIFY_API_TOKEN`, `JSEARCH_API_KEY`, `LOGO_DEV_TOKEN`.
- **Observability:** `SENTRY_DSN`.

---

## 12. Testing & CI

- **Backend:** pytest; DB-gated tests run against a Postgres service and skip
  without `TEST_DATABASE_URL`. The `db_session` fixture truncates data tables
  between tests — any new table written by an autonomous session (e.g.
  `gmail_sweep_run` via `record_sweep`) must be added to that truncate list.
- **Frontend:** vitest (unit), Playwright E2E against the Vercel preview with
  route-mocked API responses.
- **CI gate** is the aggregate **"All Checks"** job (lint, typecheck, unit, E2E,
  migration check, OpenAPI snapshot, secret scan). **SonarCloud is advisory**, not
  required. Branch protection requires branches be up-to-date before merge;
  auto-merge is disabled (merges serialize).

---

## 13. Notable design decisions

- **Outcome-driven Pipeline:** the operator's history lives in `outcome_event`
  (Gmail), not in applied-posting rows — so Pipeline/Applied/Rejected are built by
  bucketing outcomes, with manual application state unified on top.
- **Name-based company signals over id-based:** matching outcomes to companies by
  normalized name captures the unlinked majority that id-keyed matching misses;
  ambiguous names are suppressed rather than risk a false count.
- **Autonomous-session audit writes:** sweep/run records commit on their own
  session so timing/audit survives a failure rollback of the main transaction.
- **Reversible filters, not hard excludes:** the PM/PO gate and hard rules narrow
  the queue but are toggleable, because the role-family classifier is imperfect.
- **Expose before influence (Version A):** the scoring evolution shipped in
  read-only stages — A1 made `fit_score` legible (`score_components`), A2 computed
  the applied-corpus signal as a pure diagnostic, A3 blended it behind a
  default-0 weight — so every step was inspectable before it could move a score.
- **Surgical boost over weighted blend (A3 Philosophy 2):** the revealed-preference
  signal can only LIFT eligible (non-gated, non-disguised, in-target-seniority)
  roles and never buries — because the embedding is blind to seniority/negatives,
  which the heuristic caps already encode. A plain weighted-mean blend would have
  nudged senior roles up and shaved high-fit/low-sim roles down.
- **Reinstate via append, not mutate:** returning a passed role to triage appends
  a `reset` action; the original `not_interested` row (with its reason) is
  preserved, keeping the append-only audit trail intact.
- **Diagnostics as read-only endpoints + probes:** corpus questions are answered
  by hardcoded-SQL `/admin/diagnostics/*` endpoints (no SQL runner) with manual
  `workflow_dispatch` probes, since there's no direct prod DB access.

See [`DECISIONS.md`](DECISIONS.md) for the full log and [`BESTIARY.md`](BESTIARY.md)
for the bugs that shaped these choices.
