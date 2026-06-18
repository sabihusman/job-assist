# Job Assist — Architecture Map

> **Read from the actual codebase**, not a concept sketch. Every node and edge below was traced to real source with `file:line` evidence. Edges are marked **confirmed** (seen in code) or **inferred** (reasonable but not directly wired in source). Where something is ambiguous it is flagged rather than guessed.
>
> **Stack:** Next.js (Vercel) front end + same-origin API proxy → FastAPI (Railway) → Postgres + pgvector. Google **Gemini** is the only LLM (classification, summaries, embeddings). **Gmail API** + **Apify** are the external data sources. **GitHub Actions** is the cron/orchestration layer. There is no message queue and no websocket — everything is request/response.
>
> **Visual legend:** 🟦 internal app service · 🟪 LLM (Gemini) · 🟩 database · 🟧 external service/platform · 🟦(cyan) front end · dashed edge = *inferred* · red‑dashed border = *known failure point*.

---

## 0. System Overview — where everything sits and what talks to what

```mermaid
flowchart TB
  classDef fe fill:#0891b2,color:#fff,stroke:#155e75,stroke-width:1px;
  classDef svc fill:#2563eb,color:#fff,stroke:#1e40af,stroke-width:1px;
  classDef llm fill:#7c3aed,color:#fff,stroke:#4c1d95,stroke-width:1px;
  classDef db fill:#059669,color:#fff,stroke:#065f46,stroke-width:1px;
  classDef ext fill:#d97706,color:#fff,stroke:#92400e,stroke-width:1px;
  classDef fail stroke:#dc2626,stroke-width:3px,stroke-dasharray:5 3;

  subgraph Browser["🧑 Operator browser"]
    UI["Next.js React app<br/>(triage, applied, pipeline, companies, settings)"]:::fe
  end

  subgraph Vercel["▲ Vercel (Node runtime)"]
    PROXY["Same-origin API proxy<br/>/api/be/[...path]/route.ts<br/>injects bearer · strips Expect · buffers body"]:::fe
  end

  subgraph Railway["🚂 Railway"]
    API["FastAPI app (main.py)<br/>~all routes gated by 1 bearer token<br/>(/health is the only open route)"]:::svc
    ING["Ingest engine<br/>services/ingestion.py + adapters/*"]:::svc
    SCORE["Scorer (services/scoring.py)<br/>+ rescore + postings_query"]:::svc
    EMB["Embeddings (services/embeddings.py)"]:::svc
    GM["Gmail poll/classify<br/>gmail/* + outcome match"]:::svc
    PG[("Postgres + pgvector<br/>job_posting · posting_source · target_company<br/>outcome_event · application_state · posting_action<br/>operator_profile · ingest_run · discovered_handle")]:::db
  end

  GEM{{"Gemini 2.5 Flash-Lite<br/>+ gemini-embedding-001"}}:::llm
  GMAILAPI["Gmail API<br/>(read-only)"]:::ext
  APIFY["Apify<br/>Fantastic.jobs actor"]:::ext
  ATS["ATS boards<br/>Greenhouse · Lever · Ashby<br/>Workday · iCIMS"]:::ext
  GHA["GitHub Actions<br/>12 workflows (crons + CI/CD)"]:::ext

  UI -->|"openapi-fetch, baseUrl=/api/be"| PROXY
  PROXY -->|"HTTPS + Bearer (server-only token)"| API
  API -->|"JSON / xlsx stream"| PROXY
  PROXY --> UI

  API --> ING
  API --> SCORE
  API --> EMB
  API --> GM
  ING <--> PG
  SCORE <--> PG
  EMB <--> PG
  GM <--> PG
  API <--> PG

  ING -->|"fetch postings"| ATS
  ING -->|"Workday/iCIMS (IP-blocked) via proxy"| APIFY
  APIFY --> ATS
  EMB -->|"embed text → 768-d vector"| GEM
  SCORE -. "reads similarity_score" .-> PG
  GM -->|"classify emails → outcome_type"| GEM
  GM -->|"after: watermark query"| GMAILAPI
  API -->|"classify role_family / summarize JD"| GEM

  GHA -->|"scheduled POST /admin/* (Bearer)"| API
  GHA -. "auto-deploy on push (inferred webhook)" .-> Railway
  GHA -. "vercel deploy --prebuilt" .-> Vercel

  class PROXY fail
```

**Components**

- **Front end (🟦 cyan)** — `apps/web`, Next.js App Router. Pages: `/` (Triage), `/applied`, `/passed`, `/rejected`, `/companies`, `/contacts`, `/resumes`, `/pipeline`, `/settings`, `/stats` (`apps/web/src/app/*`). Talks to the back end **only** through the same-origin proxy via an `openapi-fetch` client whose `baseUrl` is `/api/be` (`apps/web/src/lib/api/client.ts:16`). URL search params are the source of truth for filter state (`apps/web/src/lib/triage/filters.ts`).
- **Vercel proxy (🟦 cyan, flagged)** — `apps/web/src/app/api/be/[...path]/route.ts`. Node runtime, `force-dynamic`. Injects the **server-only** `API_AUTH_TOKEN` bearer (never reaches the browser), strips hop-by-hop headers, buffers the body, forwards to Railway, and surfaces upstream failures as a structured `502`. This single hop is the most failure-prone edge in the system (see §5, §8).
- **FastAPI back end (🟦)** — `apps/api/src/job_assist/main.py`. One shared bearer token gates every route **except** `GET /health`. Hosts the ingest engine, scorer, embeddings, Gmail pipeline, and all admin endpoints the crons call.
- **Postgres + pgvector (🟩)** — single Railway database. Core tables: `job_posting` (with `jd_embedding vector(768)`), `posting_source`, `target_company`, `outcome_event`, `application_state`, `posting_action`, `operator_profile`, `ingest_run`, `discovered_handle`.
- **Gemini (🟪)** — the only LLM. Three call sites: posting **classifier** (`role_family`/`seniority`), **JD summaries**/company/division enrichment, and **embeddings** (`gemini-embedding-001`). Gmail outcome classification is a fourth Gemini call.
- **External (🟧)** — Gmail API (read-only), Apify (proxy crawler for IP-blocked boards), the ATS boards themselves, and GitHub Actions (cron + CI/CD).

**Edge directions** — The browser↔proxy↔API↔DB spine is two-way request/response. Ingest→ATS, EMB→Gemini, GM→Gmail, crons→API are one-way calls (the callee returns data inline). `GHA → Railway auto-deploy` and `GHA → Vercel deploy` are **inferred** from `deploy.yml` comments (a Railway webhook is referenced but not defined in-repo).

---

## 1. Ingest subsystem — adapters → dedupe → score → hard-rules → corpus

```mermaid
flowchart LR
  classDef svc fill:#2563eb,color:#fff,stroke:#1e40af;
  classDef db fill:#059669,color:#fff,stroke:#065f46;
  classDef ext fill:#d97706,color:#fff,stroke:#92400e;
  classDef llm fill:#7c3aed,color:#fff,stroke:#4c1d95;
  classDef fail stroke:#dc2626,stroke-width:3px,stroke-dasharray:5 3;

  ATS["ATS boards<br/>greenhouse/lever/ashby"]:::ext
  WD["Workday / iCIMS<br/>(datacenter-IP blocked)"]:::ext
  APIFY["Apify Fantastic.jobs"]:::ext

  subgraph ingest["ingest_source()  (services/ingestion.py)"]
    direction TB
    FETCH["adapter.fetch_postings(handle)<br/>:111"]:::svc
    PRE["title pre-filter (opt-in)<br/>should_keep_title · :115"]:::svc
    NORM["adapter.normalize()<br/>regex role_family + seniority<br/>:125"]:::svc
    UPJP["_upsert_job_posting<br/>DEDUPE on content_hash · :131,266"]:::svc
    SCO["_auto_score → score_posting()<br/>:137 (try/except, optional)"]:::svc
    HR["_eval_hard_rules → apply_hard_rules()<br/>:138 (try/except, optional)"]:::svc
    UPPS["_upsert_posting_source<br/>DEDUPE on (ats, source_job_id) · :141"]:::svc
  end

  STALE["mark_stale_postings()<br/>closed_at if unseen ≥7d · :469"]:::svc
  JP[("job_posting")]:::db
  PS[("posting_source")]:::db
  RUN[("ingest_run (status)")]:::db
  GEM{{"Gemini classifier<br/>(NOT here — separate sweep, see §2)"}}:::llm

  ATS --> FETCH
  WD -. "blocked → " .-> APIFY --> FETCH
  FETCH --> PRE --> NORM --> UPJP --> SCO --> HR --> UPPS
  UPJP <--> JP
  UPPS <--> PS
  ingest --> RUN
  STALE --> JP
  NORM -. "role_family is regex here; Gemini overwrites it later" .-> GEM

  class WD fail
```

**The real pipeline** (per posting, `services/ingestion.py`): `fetch_postings()` → optional **title pre-filter** (`should_keep_title`, on for broad-ingest, off for curated) → `normalize()` (which sets `role_family`/`seniority` from **regex heuristics** in `adapters/normalization.py`, *not* the LLM) → **upsert `job_posting`** deduped on **`content_hash`** (`sha256(company+title+locations)`, `:266`) → `_auto_score` → `_eval_hard_rules` → **upsert `posting_source`** deduped on **`(ats, source_job_id)`** (`:432`). Each posting flushes individually.

**Adapters** (`apps/api/src/job_assist/adapters/`, registry in `main.py`) implement a common `Adapter` protocol (`base.py`): `fetch_postings`, `normalize`, `peek_title`. Greenhouse/Lever/Ashby are plain JSON APIs. **Workday and iCIMS use `BROWSER_HEADERS`** (`base.py:18-25`) because their anti-bot layer rejects default `httpx` UAs — and when the block is **IP-based** (datacenter egress), headers aren't enough, which is exactly **why the Apify "Fantastic.jobs" path exists**: it crawls those boards by `domain` from residential infra (`services/fantastic_ingest.py`).

**Dedupe / lifecycle** — A posting is "already seen" by `content_hash`; re-ingest **bumps `last_seen_at`** and clears `closed_at` if it had been marked stale (`:328`). `mark_stale_postings()` sets `closed_at` when a row hasn't been seen for ≥ 7 days (`:469`); the triage list hides `closed_at IS NOT NULL` by default.

**Failure isolation (confirmed)** — `_auto_score` and `_eval_hard_rules` are each wrapped in `try/except` that logs and continues (`:382-387`, `:415-420`); a scoring or rule error **never** fails the ingest run. `fetch` errors are recorded as distinct `ingest_run.status` values (`handle_not_found` vs `failed`).

**Hidden dependency** — The LLM classifier is **not** in this path. `role_family` set here is the cheap regex guess; the Gemini classifier (a separate cron sweep, §2) overwrites it and rescores. So immediately post-ingest, `role_family`/`fit_score` reflect heuristics only.

---

## 2. Scoring & Embeddings — the `v2_semantic` blend, and the LLM's real position

```mermaid
flowchart TB
  classDef svc fill:#2563eb,color:#fff,stroke:#1e40af;
  classDef db fill:#059669,color:#fff,stroke:#065f46;
  classDef llm fill:#7c3aed,color:#fff,stroke:#4c1d95;
  classDef ext fill:#d97706,color:#fff,stroke:#92400e;

  SCO["score_posting_decomposed() — pure fn · SCORER_VERSION v2_semantic<br/>weighted mean of 6 sub-scores (Σ=100):<br/>role_family 20 · seniority 20 · salary 15 · tier 10 · geo 15 · semantic_fit 20<br/>HARD GATE: role_family not in (product_management, product_owner) → cap 40<br/>SOFT CAP: disguised-senior (PM + pm/unknown + USD salary_min ≥ 175k) → 55<br/>A1: emits score_components (full decomposition, final == fit_score)<br/>A3: applied-corpus boost AFTER caps — lift-only, eligibility-gated,<br/>behind applied_corpus_weight (default 0 = no-op)"]:::svc

  CLS["reclassify sweep (Gemini)<br/>main.py:2000+ · cron 07:30"]:::svc
  GEMC{{"Gemini 2.5 Flash-Lite<br/>classify_posting()<br/>role_family + seniority"}}:::llm
  SUM["JD summary sweep (Gemini)<br/>cron 08:30"]:::svc
  GEMS{{"Gemini Flash-Lite<br/>jd_summary_markdown"}}:::llm
  EMBS["embed sweep (sweep_embeddings)<br/>cron 09:00 · limit 200"]:::svc
  GEME{{"gemini-embedding-001<br/>768-d, L2-normalized"}}:::llm
  RECAL["recalibrate_similarity()<br/>PERCENT_RANK vs profile vector → 0..100"]:::svc
  RESC["rescore_open_postings()<br/>batched, memory-flat"]:::svc

  JP[("job_posting<br/>fit_score · score_components · role_family · jd_embedding<br/>similarity_score · jd_summary_markdown")]:::db
  PROF[("operator_profile<br/>looking_for_embedding · similarity_weight · applied_corpus_weight")]:::db

  CLS --> GEMC --> CLS
  CLS -->|"writes role_family, seniority"| JP
  CLS -->|"then rescores"| SCO
  SUM --> GEMS --> JP
  JP -->|"jd_summary or jd_text[:3000]"| EMBS --> GEME --> EMBS
  EMBS -->|"writes jd_embedding"| JP
  EMBS -->|"if embedded>0"| RECAL
  RECAL -->|"writes similarity_score"| JP
  RECAL --> RESC --> SCO
  SCO -->|"writes fit_score, scorer_version, scored_at"| JP
  PROF -. "looking_for_embedding (RETRIEVAL_QUERY)" .-> RECAL
  PROF -. "similarity_weight w (default 0)" .-> BLEND

  BLEND["best_fit_semantic SORT (postings_query.py:369-387)<br/>blended = (1-w)·fit_score + w·COALESCE(similarity_score, fit_score)"]:::svc
  JP --> BLEND
```

**Heuristic score (`fit_score`)** — `score_posting()` is a **pure function** (no I/O): a weighted mean of six 0–100 sub-scores with weights summing to 100 (`scoring.py:81-89`). Two post-adjustments: the **role-family hard gate** caps any non-PM/PO posting at **40** (`:521`), and a **disguised-senior soft cap** of **55** triggers for PM rows whose seniority is `pm`/`unknown` but whose USD `salary_min ≥ $175k` (`:529`). When `similarity_score` is NULL the `semantic_fit` term is dropped and the remaining weights **renormalize**, so un-embedded rows score on heuristics alone.

> *Note on a doc-drift:* the export's context sheet prose still says "five sub-scores"; the live `_WEIGHTS` dict has **six** (`semantic_fit` was added with `v2_semantic`). The code is authoritative.

**Where the LLM sits (confirmed, and this is the non-obvious part)** — The **classifier is decoupled from ingest**. `classify_posting()` (`services/classifier.py`, model `gemini-2.5-flash-lite`, `temperature=0`, JSON output) is called only by the **reclassify sweep** (`main.py:2000+`, the `enrich-classifier` cron at 07:30). It takes `jd_text[:3000]` + `normalized_title` (+ the operator's profile text *for disambiguation only*), returns `(role_family, seniority_level)`, **overwrites** the regex values, and then **rescores** the row (because role_family+seniority are 40% of the weight). On any failure it falls back to `("other","unknown")` and the row is skipped — never fatal.

**Semantic signal** — The `embed-postings` cron (09:00) runs `sweep_embeddings()`: selects open rows whose vector is missing/stale and under the 3-attempt cap, picks **`jd_summary_markdown` if ≥100 chars else `jd_text[:3000]`**, calls `gemini-embedding-001` (768-d, `RETRIEVAL_DOCUMENT`, L2-normalized), writes `jd_embedding`. **If anything embedded**, it triggers `recalibrate_similarity()` — a single SQL pass computing `similarity_score = ROUND(100 · PERCENT_RANK() OVER (ORDER BY cosine_distance(profile_vector) DESC))` across the open corpus — then `rescore_open_postings()`. The profile vector is the operator's `looking_for_embedding` (`RETRIEVAL_QUERY`), re-embedded on profile save, which **also** triggers recalibrate+rescore (`main.py` profile hook).

**The blend** — The `best_fit_semantic` sort (`postings_query.py:369-387`) is `blended = (1-w)·fit_score + w·COALESCE(similarity_score, fit_score)`, where `w = operator_profile.similarity_weight` (**default 0**). At `w=0` it is byte-identical to `best_fit`; un-embedded rows fall back to `fit_score`, so no row ever gets a fake semantic signal.

**Hidden dependency / failure point** — **Embedding-timing**: `similarity_score` is NULL until the embed sweep *and* recalibration run. The whole semantic feature degrades gracefully to heuristics via the `COALESCE` fallback, but a stalled embed cron silently means "semantic off." The enrichment crons also form an **ordered chain** (company 07:00 → classifier 07:30 → divisions 08:00 → JD-summaries 08:30 → embeddings 09:00); each is idempotent so a stall doesn't block downstream, but downstream runs on staler inputs (e.g. embeddings fall back from summary to raw JD). Gemini **rate limits** and the 3-attempt cap mean a backlog drains over successive days rather than in one run.

---

## 3. Gmail → Outcomes → Pipeline → Applied view (with the no-fanout guard)

```mermaid
flowchart TB
  classDef svc fill:#2563eb,color:#fff,stroke:#1e40af;
  classDef db fill:#059669,color:#fff,stroke:#065f46;
  classDef llm fill:#7c3aed,color:#fff,stroke:#4c1d95;
  classDef ext fill:#d97706,color:#fff,stroke:#92400e;
  classDef fe fill:#0891b2,color:#fff,stroke:#155e75;
  classDef fail stroke:#dc2626,stroke-width:3px,stroke-dasharray:5 3;

  CRON["gmail-poll cron (every 6h)<br/>POST /admin/gmail/poll"]:::ext
  POLL["run_poll() (gmail/backfill.py)<br/>watermark = MAX(outcome_event.received_at)<br/>fallback now-24h"]:::svc
  GAPI["Gmail API (readonly)<br/>after:&lt;watermark&gt;"]:::ext
  GCLS["EmailClassifier (gmail/classifier.py)"]:::svc
  GEM{{"Gemini 2.5 Flash-Lite<br/>→ outcome_type (13 categories)"}}:::llm
  OE[("outcome_event<br/>email_message_id UNIQUE<br/>job_posting_id = NULL (deferred)<br/>target_company_id (by domain)")]:::db
  RELINK["outcome relink → company"]:::svc
  MATCH["outcome_posting_match.py<br/>role-token link, score ≥ 0.6"]:::svc

  AS[("application_state<br/>applied→interview→offer→accepted/rejected")]:::db
  PA[("posting_action<br/>(triage; action_type='applied')")]:::db
  RSX["resolved_status_expr()<br/>COALESCE(manual status, posting_action='applied')<br/>POSTING-SPECIFIC — no company fanout"]:::svc

  UNI["unifyApplied() (apps/web/.../applied/unify.ts)<br/>dedupe: posting:&lt;id&gt; / thread / outcome"]:::fe
  VIEW["Applied page + UnifiedAppliedRow<br/>source chip: Manual / Gmail / both"]:::fe

  CRON --> POLL --> GAPI --> GCLS --> GEM --> GCLS
  GCLS -->|"insert (idempotent on email_message_id)"| OE
  OE --> RELINK -->|"target_company_id"| OE
  POLL -. "tail: best-effort" .-> MATCH
  MATCH -->|"job_posting_id only on confident ROLE match"| OE

  AS --> RSX
  PA --> RSX
  RSX -->|"drives Applied/Rejected tab membership"| VIEW
  OE -->|"GET /outcomes (job_related)"| UNI
  AS -. "manual_status overlay" .-> UNI
  UNI --> VIEW

  class OE fail
```

**Poll → classify → store** — The `gmail-poll` cron (now **every 6 h**, `0 */6 * * *`) calls `POST /admin/gmail/poll` → `run_poll()`. The Gmail query window is `after:<watermark>` where **watermark = `MAX(outcome_event.received_at)`** (data-derived, no state table; 24 h bootstrap when empty). New messages are classified by **Gemini 2.5 Flash-Lite** (`gmail/classifier.py`, `temperature=0`, JSON) into one of 13 `outcome_type`s, and inserted as `outcome_event` rows. **Idempotency** is the `email_message_id` UNIQUE constraint plus an in-run pre-check.

**The no-fanout guard (the important, non-obvious invariant)** — `outcome_event.job_posting_id` is **NULL by design** in the poll path: Gmail can tie an email to a *company* (by domain) but not to a *specific role*. A company can have many open postings, so folding a company-level "application confirmed" email into tab membership would **fan one email out across every role at that company** (the historical bug: passed-and-never-seen roles appearing in Applied). The guard lives in `services/postings_query.py:resolved_status_expr()`: Applied/Rejected membership is driven **only** by a **posting-specific** signal — the operator's manual `application_state` (authoritative via `COALESCE`) or an explicit `posting_action='applied'` on *that* role. Gmail rejections survive as an **informational hint** field only (`gmail_rejection_exists()`), never as membership. A separate best-effort step (`outcome_posting_match.py`) *may* set `job_posting_id` — but only on a confident **role-token** match (score ≥ 0.6), purely for navigation.

**Pipeline + Applied view** — `application_state` holds the manual lifecycle (`applied→interview→offer→accepted/rejected`; `applied_at` stamped once). The Applied page calls `GET /outcomes?job_related=true` and the active funnel, and `unifyApplied()` (`apps/web/src/lib/applied/unify.ts`) fuses them: it groups Gmail outcomes by thread, re-keys by linked posting (`posting:<id>`) so two threads for one role collapse, and overlays the **manual status as the winner**. `UnifiedAppliedRow` shows a Manual/Gmail/**both** source chip.

**Failure points** — OAuth refresh token (`GMAIL_REFRESH_TOKEN`) auto-refreshes on 401; a **~7-day re-auth cycle is inferred** from Google's standard flow (not enforced in-repo). Gemini rate limits throttle classification (~4 s/request). The watermark design means a missed poll window self-heals on the next run.

---

## 4. Self-maintaining loop — crons → ingest → health-check → alert

```mermaid
flowchart TB
  classDef ext fill:#d97706,color:#fff,stroke:#92400e;
  classDef svc fill:#2563eb,color:#fff,stroke:#1e40af;
  classDef db fill:#059669,color:#fff,stroke:#065f46;
  classDef fe fill:#0891b2,color:#fff,stroke:#155e75;

  subgraph daily["Nightly chain (UTC) — GitHub Actions"]
    direction TB
    C1["06:00 ingest-daily<br/>curated ATS + Apify + mark-stale"]:::ext
    C2["06:30 broad-ingest<br/>10 paced calls, weekly_cap"]:::ext
    C3["07:00 enrich-companies"]:::ext
    C4["07:30 enrich-classifier (Gemini)"]:::ext
    C5["08:00 enrich-divisions"]:::ext
    C6["08:30 enrich-jd-summaries (Gemini)"]:::ext
    C7["09:00 embed-postings (Gemini)"]:::ext
    C1-->C2-->C3-->C4-->C5-->C6-->C7
  end
  KA["keepalive — every 5 min<br/>GET /health (no auth)"]:::ext
  GP["gmail-poll — every 6h"]:::ext

  API["FastAPI /admin/* (Bearer)"]:::svc
  PG[("ingest_run · discovered_handle · job_posting")]:::db

  HEP["GET /admin/ingest/health<br/>severity = ok | degraded | down"]:::svc
  HC["ingest-health — 09:30<br/>+ cron-health — 08:00"]:::ext
  ISSUE["GitHub Issue (idempotent)"]:::ext
  MAIL["SMTP email<br/>dawidd6/action-send-mail @pinned-SHA"]:::ext
  DOT["HealthDot (front end)<br/>polls /health endpoint every 60s<br/>🟢 ok 🟡 degraded 🔴 down"]:::fe

  daily --> API
  KA --> API
  GP --> API
  API <--> PG
  HEP --> PG
  HC -->|"GET health (Bearer)"| HEP
  HC -->|"if unhealthy"| ISSUE
  HC -. "if MAIL_* set" .-> MAIL
  HC -->|"exit 1 → GitHub run-failed email"| HC
  DOT -->|"every 60s via proxy"| HEP
```

**Health = dead-man's-switch** — `GET /admin/ingest/health` (`main.py`) computes a **severity** from four checks over fixed windows (`_HEALTH_RECENT_HOURS = 26`, `_HEALTH_STARVATION_DAYS = 3`):

| Check | Meaning | Weight |
|---|---|---|
| `recent_success` | a successful `ingest_run` within 26 h (daily cron ran) | **hard** → `down` if false |
| `no_hard_failures` | zero `failed` runs in 26 h | **hard** → `down` if false |
| `broad_fresh` | a `discovered_handle` swept within 26 h (broad cron ran) | soft → `degraded` |
| `not_starved` | ≥ 1 net-new posting in 3 days | soft → `degraded` |

`severity = down if any hard fails, else degraded if any soft fails, else ok`.

**Alerting** — `ingest-health.yml` (09:30) curls the endpoint; on unhealthy it opens an **idempotent GitHub Issue**, optionally emails via `dawidd6/action-send-mail` **pinned to a commit SHA**, and `exit 1`s so GitHub's own "workflow failed" mail fires. `cron-health.yml` (08:00) is a parallel guard over `/admin/cron-status`. The front-end **HealthDot** (`components/chrome/HealthDot.tsx`, hook `lib/api/health.ts`) polls every 60 s and renders a 🟢/🟡/🔴 dot. **keepalive** (every 5 min, `GET /health`, no auth) prevents Railway cold-starts.

**Failure points** — A **cron-gap** (a workflow that silently stops firing) is exactly what the 26 h windows + dead-man's-switch catch. The alert path itself depends on GitHub Actions being up and `MAIL_*` secrets being set (email is best-effort; the Issue + run-failure are not).

---

## 5. Proxy / request path — the most failure-prone hop

```mermaid
flowchart LR
  classDef fe fill:#0891b2,color:#fff,stroke:#155e75;
  classDef svc fill:#2563eb,color:#fff,stroke:#1e40af;
  classDef db fill:#059669,color:#fff,stroke:#065f46;
  classDef fail stroke:#dc2626,stroke-width:3px,stroke-dasharray:5 3;

  B["Browser fetch<br/>/api/be/postings?... (same origin)"]:::fe
  subgraph P["proxy route.ts (Vercel, Node runtime)"]
    direction TB
    S1["strip hop-by-hop headers<br/>host · connection · content-length · <b>expect</b>"]:::fe
    S2["buffer body via req.arrayBuffer()<br/>(NO duplex streaming)"]:::fe
    S3["inject Authorization: Bearer API_AUTH_TOKEN<br/>(server-only env)"]:::fe
    S4["fetch(upstream) in try/catch<br/>→ 502 {detail,error,upstream_path,method}"]:::fe
    S1-->S2-->S3-->S4
  end
  API["Railway FastAPI<br/>auth_guard → route"]:::svc
  DB[("Postgres")]:::db

  B --> S1
  S4 -->|"HTTPS"| API --> DB
  API -->|"JSON / xlsx (Content-Disposition preserved)"| S4
  S4 --> B

  class P fail
```

**Why this hop is special** — The browser never holds the API token; it calls the **same-origin** `/api/be/[...path]`, and the proxy injects the bearer server-side. Two non-obvious bugs lived here (both fixed this session):

1. **`Expect: 100-continue`** — clients (curl/.NET/PowerShell, and some browsers) send this on POST bodies. undici's `fetch` **rejects any request carrying an `Expect` header** (`NotSupportedError: expect header not supported`), so **every write through the proxy failed with an opaque empty 500** while reads and direct-to-Railway curls worked. Fix: add `expect` to `STRIP_REQUEST_HEADERS`.
2. **Streamed body reset** — forwarding `req.body` as a `duplex:'half'` stream intermittently reset the connection on writes. Fix: **buffer** with `req.arrayBuffer()` and let undici set `Content-Length`.

The proxy now wraps the upstream `fetch` in try/catch and returns a structured **`502`** (`{detail, error, upstream_path, method}`) instead of an undiagnosable empty 500 — this is what surfaced the Expect error in the first place.

---

## 6. Lifecycle trace A — a job: ingest → classify → score → embed → triage → export

```mermaid
sequenceDiagram
  autonumber
  participant CRON as GitHub Actions (crons)
  participant API as FastAPI
  participant ATS as ATS / Apify
  participant LLM as Gemini (LLM)
  participant DB as Postgres+pgvector
  participant UI as Browser (via proxy)

  Note over CRON,DB: 06:00 — ingest
  CRON->>API: POST /admin/ingest/{ats}/{handle}
  API->>ATS: fetch_postings()
  ATS-->>API: raw postings
  API->>API: normalize() — regex role_family/seniority
  API->>DB: upsert job_posting (dedupe content_hash) + posting_source
  API->>API: _auto_score (heuristic fit_score) + hard_rules
  API->>DB: write fit_score, hard_rule_failed

  Note over CRON,LLM: 07:30 — classifier sweep (LLM enters HERE)
  CRON->>API: POST /admin/reclassify/sweep
  API->>LLM: classify_posting(jd_text[:3000], title)
  LLM-->>API: role_family + seniority (one-way, JSON)
  API->>DB: overwrite role_family/seniority
  API->>API: rescore (role_family is 20% weight + the 40-cap gate)
  API->>DB: write fit_score'

  Note over CRON,LLM: 08:30 → 09:00 — summary + embedding
  CRON->>API: POST /enrichment/jd-summaries/sweep
  API->>LLM: summarize JD
  LLM-->>API: jd_summary_markdown
  API->>DB: write summary
  CRON->>API: POST /admin/embeddings/sweep
  API->>LLM: embed(summary or jd_text)  → 768-d vector
  LLM-->>API: vector
  API->>DB: write jd_embedding
  API->>DB: recalibrate_similarity (PERCENT_RANK) → similarity_score
  API->>API: rescore_open_postings
  API->>DB: write fit_score''

  Note over UI,DB: operator works the queue
  UI->>API: GET /postings?state=triage&sort=best_fit_semantic (via proxy)
  API->>DB: build_view_parts(spec) — WHERE + ORDER BY + per-company cap
  DB-->>API: rows
  API-->>UI: ranked postings
  UI->>API: GET /postings/export.xlsx?<same filters> (via proxy)
  API->>DB: SAME query, NO limit
  API-->>UI: xlsx (current filtered view)
```

**LLM position (explicit):** the LLM is called **three times, all asynchronously and all after ingest** — (1) the **classifier** sweep that *replaces* the regex `role_family` and *feeds the scorer*, (2) the **JD-summary** sweep that *feeds the embedder*, (3) the **embedder** whose vector *feeds `similarity_score` → the blend*. Every LLM call is **one-way** (request returns JSON inline; nothing downstream calls back into the LLM). The operator-facing triage list and the export read the **same** `build_view_parts()` query (the export is just the list without `LIMIT`).

---

## 7. Lifecycle trace B — a Gmail confirmation → outcome → pipeline → applied view

```mermaid
sequenceDiagram
  autonumber
  participant CRON as gmail-poll (every 6h)
  participant API as FastAPI
  participant GAPI as Gmail API
  participant LLM as Gemini (LLM)
  participant DB as Postgres
  participant UI as Browser (Applied page)

  CRON->>API: POST /admin/gmail/poll
  API->>DB: watermark = MAX(outcome_event.received_at)
  API->>GAPI: messages after:<watermark>
  GAPI-->>API: new messages
  API->>LLM: classify(from, subject, body[:2000])
  LLM-->>API: outcome_type + confidence (one-way, JSON)
  API->>DB: insert outcome_event (idempotent on email_message_id)<br/>job_posting_id = NULL, target_company_id by domain
  API->>API: outcome_posting_match (role-token ≥ 0.6) — best effort
  API-->>DB: maybe set job_posting_id (navigation only)

  Note over UI,DB: NO fan-out — company email never sets tab membership
  UI->>API: GET /outcomes?job_related=true  (+ active funnel)
  API->>DB: join outcome_event ⋈ company ⋈ application_state ⋈ posting
  DB-->>API: rows (+ manual_status overlay)
  API-->>UI: outcomes + manual postings
  UI->>UI: unifyApplied() — dedupe posting/thread, manual status wins
  UI-->>UI: render Applied rows (chip: Manual / Gmail / both)

  Note over UI,DB: tab membership is posting-specific
  UI->>API: PUT /postings/{id}/status = interview
  API->>DB: upsert application_state (applied_at stamped once)
```

**LLM position (explicit):** exactly **one** LLM call — the **Gmail outcome classifier** turns each email into an `outcome_type`. Its output lands in `outcome_event` but is deliberately **firewalled** from Applied/Rejected tab membership (the no-fanout guard); only the operator's manual `application_state`/`posting_action` decides membership. The LLM is again **one-way**.

---

## 8. Hidden dependencies & failure points (the things that bite)

```mermaid
flowchart TB
  classDef fail fill:#7f1d1d,color:#fff,stroke:#dc2626,stroke-width:2px;
  classDef warn fill:#78350f,color:#fff,stroke:#f59e0b,stroke-width:2px;
  classDef note fill:#334155,color:#fff,stroke:#64748b;

  F1["PROXY EXPECT-HEADER (fixed)<br/>undici rejects Expect:100-continue<br/>→ every write = opaque 500"]:::fail
  F2["PROXY BODY STREAM (fixed)<br/>duplex stream reset on writes<br/>→ buffer via arrayBuffer"]:::fail
  F3["RAILWAY ~525KB BODY CAP (latent)<br/>edge proxy 400 'error parsing body'<br/>→ batch large seeds ~100 rows"]:::warn
  F4["WORKDAY/iCIMS IP-BLOCK<br/>datacenter egress blocked<br/>→ Apify exists (real cost)"]:::warn
  F5["EMBEDDING TIMING<br/>similarity_score NULL until embed+recalibrate<br/>→ semantic silently = heuristic"]:::warn
  F6["CRON-GAP<br/>a workflow stops firing<br/>→ caught by 26h dead-man's-switch"]:::warn
  F7["GEMINI RATE / 3-ATTEMPT CAP<br/>backlog drains over days"]:::warn
  F8["MERGED ≠ PROD<br/>Railway+Vercel deploy after merge<br/>CI green ≠ live; verify post-deploy"]:::warn
  F9["MEMORY ON WRITE BURSTS<br/>rescore/embeddings use batched commits<br/>+ ORM expunge to stay flat"]:::note
```

- **Proxy Expect-header (fixed, §5)** — the session's headline bug: writes-only failure with empty 500. *Lesson: the proxy hop is data-shaped, not just a pass-through.*
- **Proxy body streaming (fixed, §5)** — duplex streaming reset writes; now buffered.
- **Railway ~525 KB body cap (latent)** — the edge proxy returns a generic `400 "There was an error parsing the body"` above ~525 KB; mitigation is to batch large seeds (~100 rows). Not in the hot path, but it bit the contacts seed and is documented in `docs/BESTIARY.md` 5.15.
- **Workday/iCIMS IP-blocking** — these boards block datacenter egress, which is the entire reason the **Apify** path exists; Apify carries **real per-call cost**, mitigated by a PM/PO title filter at ingest.
- **Embedding-timing** — `similarity_score` is NULL until the embed sweep + recalibration land; `best_fit_semantic` degrades to heuristics via `COALESCE`. A stalled embed cron = "semantic silently off."
- **Cron-gap** — the dead-man's-switch (26 h windows) is the explicit defense.
- **Gemini rate / attempt caps** — sweeps are limited (50–200 rows) and capped at 3 attempts/row; backlogs drain across days.
- **Merged ≠ working-in-prod** — CI green only proves the build; Railway (API) and Vercel (web) deploy *after* merge, and the proxy/runtime can still fail in prod (exactly how the Expect bug surfaced). Always verify a real write through the proxy post-deploy.
- **Memory on write bursts** — the originally-suspected "OOM" was a mis-diagnosis (the real write failure was the proxy Expect header); the genuine memory design is `rescore_open_postings()`/`sweep_embeddings()` committing in **batches** and expunging ORM objects to keep memory flat.

---

## 9. Legend & accuracy notes

**Visual encoding**

| Encoding | Meaning |
|---|---|
| 🟦 blue node | internal back-end service (FastAPI module) |
| 🟦 cyan node | front end / proxy |
| 🟪 purple hexagon | **LLM** (Gemini — classify / summarize / embed) |
| 🟩 green cylinder | **database** (Postgres + pgvector) |
| 🟧 amber node | **external** service/platform (Gmail, Apify, ATS, GitHub Actions) |
| solid `-->` | confirmed one-way call (callee returns inline) |
| `<-->` | confirmed two-way (request/response with DB read+write) |
| dashed `-.->` | **inferred** edge (not directly wired in source) |
| red-dashed border | known failure point |

**Confirmed vs inferred**

- **Confirmed** (quoted from source): the entire ingest pipeline + dedupe keys; the 6-weight scorer + 40-cap gate + 55 soft-cap; the embedding/recalibrate/rescore chain and the `best_fit_semantic` formula; the LLM being a *separate sweep* (not inline in ingest); the Gmail watermark + idempotency + **no-fanout guard**; the proxy strip-set (incl. `expect`), body buffering, and 502 surfacing; the health severity logic + windows; all cron schedules and targets; the auth boundary (one bearer token, `/health` open).
- **Inferred** (reasonable, not wired in-repo): the **Railway auto-deploy webhook** (referenced in `deploy.yml` comments only); the **~7-day Gmail OAuth re-auth** cadence (Google's standard flow); some **enrichment-ordering dependencies** read from cron *times* rather than explicit code gates; the exact prod hostname `api-production-ca5ad.up.railway.app` (from this session's operations, not committed config).
- **Ambiguous / flagged:** the export context-sheet prose says "five sub-scores" while the live scorer has six — treated as doc-drift, code is authoritative.

> If you change a wire in code, change it here too — this map is only as honest as its last trace.
