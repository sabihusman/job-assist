# Job Assist Bestiary

A living catalog of recurring bugs, anti-patterns, and operational gotchas discovered while building Job Assist. Each entry is a lesson paid for in a CI red — write it down so the same lesson doesn't get paid twice.

## How to use this document

**For Claude Code (and human contributors):** read this file as part of any Read-First audit before writing code. The relevant entries are the ones that touch your PR's surface area (database, mutations, tests, etc.). When in doubt, skim the section headers.

**For the operator:** add a new entry at the end of any session that surfaced a non-obvious bug. Each entry needs: title, what happened, the lesson, the PR or session where it was discovered, and (if useful) a small example. Keep entries tight — this is reference material, not narrative.

**Discovered-in tags** reference PR numbers from the internal tracker (e.g. PR #48 = classifier improvement). GitHub PR numbers differ; the internal numbers are the canonical source.

---

## 1. Test Convention Bestiary

### 1.1 NOT NULL columns are silently load-bearing

When duplicating a test fixture for a related table, copy the full factory body from the canonical test file. If no factory exists, read the model file (`posting_source.py`, `contact.py`, etc.) and enumerate every `nullable=False` column before writing the fixture. Memory-driven field lists ping-pong: missing fields surface one at a time on CI, requiring two or three red-then-green cycles to converge.

**Discovered in:** PR #55 (iCIMS adapter — `posting_source` fixture missed `source_job_id`, then `raw_payload`, then `parser_version` across successive CI runs).

**Rule:** read the model first, enumerate NOT NULL columns once, write the fixture once.

### 1.2 Migration-seeded singleton rows are shared session invariants

Tables seeded by an Alembic migration at session start (today: `operator_profile`) are intentionally absent from conftest's truncate-between-tests list. The seed row is shared session state — every test in the suite assumes it exists.

Two corollaries:

- **Don't mutate or delete migration-seeded rows in a test.** Even with try/finally restoration, the window between delete and restore is visible to any concurrently-running test, and a mid-flight test failure leaves the suite poisoned.
- **Don't write integration tests for one-line endpoint guards.** Guards like `if x is None: raise HTTPException(...)` are exhaustively covered by unit-level mocking of the dependency. The integration test adds coverage of FastAPI's routing layer that's already covered elsewhere, at the cost of breaking a shared invariant.

**Discovered in:** PR #56 (heuristic scoring — a test deleted the `operator_profile` seed row to exercise an unseeded path; broke 8 other tests across the suite).

### 1.3 When a test setup requires breaking a convention the suite relies on, the test is wrong

This is the meta-rule that covers 1.2 and several others. If exercising a code path requires breaking a shared invariant (deleting seed rows, scoping outside the surface under test, mutating singleton state), the test is wrong, not the convention. The behavior is verifiable other ways — usually via unit-level mocking.

### 1.4 Mobile-viewport E2E tests scope to the surface under test

A 380px full-page render tests AppShell + Sidebar + Banner responsiveness, not the new component. If the chrome isn't responsive yet (and at time of writing, it isn't), mobile-safe properties of a new component get proven at the unit layer — rendered class, aria attributes, parent container behavior — not via a full-page E2E that depends on chrome we haven't fixed.

Three corollaries:

- **Unit-layer proofs of mobile-safe rendering count.** Asserting that a badge sits inside a `flex-wrap` container is a valid mobile-safety contract — the runtime viewport doesn't change that fact.
- **Failed E2E at viewport boundaries should ask "what broke?" before being treated as the feature's failure.** PR #57's badge didn't fail at 380px; AppShell did, and AppShell wasn't in scope.
- **The deferred test is real future work, not abandoned work.** When AppShell becomes responsive (V2 mobile retrofit), the full-page mobile E2E gets enabled, not rewritten.

**Discovered in:** PR #57 (FitScoreBadge — full-page 380px E2E failed because AppShell's 224px sidebar left only 156px for the card, collapsing the layout. Badge itself rendered correctly).

### 1.5 Hand-authored fixture banner pattern

When tests cannot reach real upstream data (e.g. an ATS adapter has no production handle yet), the fixtures are an *assumption test*, not a *contract test*. Mark them explicitly and document the post-merge verification step.

Every fixture file carries a banner comment:

```
# HAND-AUTHORED FIXTURE — VERIFY AGAINST FIRST REAL <upstream> PAGE AFTER MERGE
```

The adapter docstring carries a matching note explaining why "tests pass + ingest produces zero rows" is a possible first-run outcome. The real contract test runs the first time the adapter points at a real upstream.

**Discovered in:** PR #55 (iCIMS adapter — no production iCIMS-flagged companies existed at merge time; fixtures were authored from documented HTML+JSON-LD structure rather than captured from real pages).

### 1.6 Positive equality assertions only

Never assert "X is NOT in supported set" — use positive equality assertions instead. The former breaks every time the set is expanded by a sibling PR; the latter survives.

Example:

```python
# Wrong — breaks when new sort options are added
assert sort_key not in {"alphabetical", "random", "made_up"}

# Right
assert sort_key in {"newest", "oldest", "salary_high_to_low", "tier", "recently_posted"}
```

**Discovered in:** PR #49 onward (broke 3 times before being formalized).

### 1.7 OpenAPI snapshot regen on Windows requires LF + trailing newline

Default Windows text-mode `open(path, "w").write()` writes CRLF line endings. Linux CI's `app.openapi()` writes LF + trailing `\n`. The byte-level snapshot diff fails the drift check even when JSON content is semantically identical.

Two paths:

- Regenerate the snapshot only inside WSL/Linux container.
- Ship a `scripts/regen_openapi.py` wrapper that opens binary mode (`"wb"`) and writes `json.dumps(...).encode() + b"\n"`.

The wrapper is the more durable fix.

**Discovered in:** Hotfix PR (incident: dynamic CTE prepared-statement collision — required a one-line `main.py` change but the OpenAPI snapshot regen on Windows produced a diff that failed CI three times before the CRLF gremlin was caught).

---

## 2. Backend / Database Bestiary

### 2.1 Every paginated ORDER BY needs a stable secondary key

`id ASC` (or another deterministic field) as the universal tiebreaker on every paginated read endpoint. Without it, rows with identical primary-sort values shuffle between pages.

**Discovered in:** PR #49 (sort options — first endpoint where this mattered, then promoted to repo-wide convention).

**Rule:** every `ORDER BY` ends with `id ASC` (or equivalent stable key).

### 2.2 Explicit IN lists over LIKE patterns in SQL filters

`outcome_type IN ('rejection_pre_screen', 'rejection_post_screen', 'rejection_post_interview')` over `outcome_type LIKE 'rejection_%'`. LIKE patterns drift silently when new enum values are added; the IN list forces a code change at the right surface (the filter), where the conversation about whether the new value belongs in the set is most useful.

**Discovered in:** PR #50 (`/rejected` page — choice of how to scope rejection outcomes).

### 2.3 Append-only event tables — current state = latest row

`posting_action` and `outreach_message` are append-only. Current state is computed via a LATERAL subquery picking the most-recent row per parent:

```python
latest_action = (
    select(...)
    .where(pa_alias.parent_id == ParentModel.id)
    .order_by(pa_alias.created_at.desc())
    .limit(1)
    .lateral("latest")
)
```

No "edit" or "delete" UI on event rows. Corrections are logged as new rows. Same pattern propagates to any future event table.

**Discovered in:** PR #25 (initial `posting_action` schema); reinforced in PR #52 (`outreach_message` follows same shape).

### 2.4 2-query budget on read endpoints

Read endpoints fold into 2 SQL queries max: one COUNT and one SELECT with LATERAL joins. Enforced in tests via `_ExecuteCounter`. CTEs that filter the SELECT also filter the COUNT — they don't add a third query.

**Discovered in:** PR #28 (read-endpoint refactor); enforced ever since.

### 2.5 State as a frontend concept can derive from multiple backend tables

When `state=rejected` on `/postings` derives from `outcome_event` (Gmail-parsed rejection emails) while `state=not_interested` derives from `posting_action` (operator passes), the endpoint's `state` filter spans two tables. Leave a comment block in the endpoint explaining the dual-table semantics so the next reader doesn't assume `state` is 1:1 with one source table.

**Discovered in:** PR #50 (`/rejected` page — first time a frontend-vocabulary state crossed table boundaries).

### 2.6 Postgres ENUM extension requires Alembic `autocommit_block`

`ALTER TYPE <type> ADD VALUE <value>` cannot run inside a transaction in Postgres. Alembic's standard auto-transaction wraps every migration in one, so plain `op.execute("ALTER TYPE...")` fails.

The correct pattern:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ats_type ADD VALUE IF NOT EXISTS 'icims'")
```

Downgrade is a no-op — Postgres doesn't support removing enum values without recreating the type.

**Discovered in:** PR #55 (iCIMS adapter — adding `'icims'` to `ats_type` enum).

### 2.7 Dynamic CTEs + asyncpg + Supabase Pooler = prepared statement collisions

Any query path that generates per-call SQL variants asyncpg will cache by sequential statement name (`__asyncpg_stmt_2__`, etc.) will fail when Supabase's Transaction-mode pooler rotates connections. The stale prepared statement on a recycled connection collides with the new request's attempt to prepare under the same name.

This is a symptom of a deeper root cause (see 5.5 — engine-level `statement_cache_size`). PR #58's CTE made the collision MORE frequent because dynamic SQL variants increase prepared-statement turnover, but the bug is latent on any query path.

**Discovered in:** PR #58 hotfix (per-company cap CTE on `/postings` returned 500 in production while passing all local tests — local Postgres has no pooler).

**Rule:** load-test dynamic-query-shape features against the deployed pooler before merge, or default them to OFF.

### 2.8 Default values on dynamic-query-shape features should default to OFF

PR #58's per-company cap defaulted to 3. Every UI call hit the broken code path. If the default had been 0 (opt-in cap), the bug would have affected only operator-flag-set requests instead of all production traffic.

**Discovered in:** PR #58 hotfix.

**Rule:** new query-shape-altering parameters default to disabled / no-op until proven safe at production scale.

### 2.9 Named constants over magic numbers

`ANNUAL_HOURS = 2080` (used to convert hourly salaries to annual) sits at the top of the scoring service, not inline in a multiplication. One-place edit if the assumption ever changes.

**Discovered in:** PR #56 (heuristic scoring — salary feature extractor).

### 2.10 Migration-seeded singletons can't be tested by deletion

(Cross-reference 1.2 — same lesson stated from the schema side.) Tables with exactly-one seeded row are shared session state. Tests that need to assert "unseeded behavior" either roll the seed back at end (brittle if the test fails mid-flight) or accept they can't be expressed safely.

---

## 3. Frontend / Mutation Bestiary

### 3.1 Wire-shape contract tests for mutations

Every mutation hook (`useRecordAction`, `useContactUpdate`, `useOutreachLog`, etc.) needs a unit test asserting the literal request body shape. Two assertions per test:

- **Present:** canonical API field names are in the request body (`action_type`, not `kind`).
- **Absent:** legacy or wrong field names are NOT in the request body.

Example:

```typescript
expect(opts.body).toHaveProperty('action_type', 'applied');
expect(opts.body).not.toHaveProperty('kind');
```

**Discovered in:** PR #58 (Vanta pass-action bug — the deployed frontend POSTed `{kind, reason}`; FastAPI demanded `{action_type, reason}`. The 422 silently rolled back optimistic UI to "phantom success." Zero pass / apply / reject / snooze actions persisted from the UI for weeks).

**Rule:** never ship a new mutation without the wire-shape contract test.

### 3.2 Silent placeholder fields are landmines

When adding a new field to a response shape, grep for the field name across the codebase before considering it shipped. If the ORM model has it but the serializer pins it to `None` as a placeholder, the field looks plumbed but is actually returning empty — and no one notices until something tries to consume it.

**Discovered in:** PR #57 (FitScoreBadge wiring — PR #56 added `fit_score` to model + Pydantic schema, but the response serializer in `main.py` hardcoded `"score": None` as a placeholder).

**Rule:** new response field → grep the codebase for the field name → confirm serializer references it.

### 3.3 Structured error responses surfaced inline in toasts

When a mutation fails, the API returns a structured body like `{"detail": "reason_required_for_not_interested"}` or `{"detail": [{"msg": "field required", ...}]}`. The frontend's onError handler reads the `detail` field via `extractDetail()` and shows it in the toast.

Generic toasts like "Action failed — try again" mask the real error. PR #58's bug hid for weeks because the toast didn't surface the 422 detail.

The canonical implementation lives in `apps/web/src/lib/api/mutation-error.ts` (typed `MutationError` + `extractDetail` helper).

**Discovered in:** PR #58 post-mortem.

**Rule:** every mutation hook throws a `MutationError` carrying `{kind, status, detail, message}`; the page-level `onError` handler surfaces `detail` verbatim.

### 3.4 Mobile-first is a forward-looking convention

From PR #57 onward, every new frontend surface is designed for mobile (~380px viewport) first, then expands at larger viewports. Existing pages stay as-is; this is *not* a retrofit. The chrome (AppShell + Sidebar + Banner) remains desktop-only until a dedicated retrofit pass.

**Discovered in:** PR #57 (FitScoreBadge — first surface where the convention was applied).

**Rule:** new surface → mobile-first. Old surface → leave it alone unless explicitly scoped for retrofit.

### 3.5 AppShell title lives in `<Banner>`, not `<main>`

Page-title E2E assertions must scope to `page.getByRole('banner')`, not `mainContent(page)`. Plus: every new sidebar entry retroactively breaks unscoped title queries on its own page — the title string now appears in both the banner and the sidebar nav.

Two rules:

- Don't assert page title inside `mainContent(page)` — it's never there.
- Don't assert page title with an unscoped query when the string also appears in sidebar nav (true for every nav-registered page).

Row content (company name, role title, reason chip, empty-state testid) is the canonical "page rendered" proof.

**Discovered in:** PR #50 (`/passed` and `/rejected` pages — adding sidebar entries collided with title queries that worked on Triage but no longer worked on the new pages).

### 3.6 `prefers-reduced-motion` guard on any animation

Forward-looking convention: any animated transition needs a CSS media-query fallback that disables motion entirely. One-line CSS per animation. Must be present in every PR that adds motion.

**Discovered in:** PR #57 + anime.js audit (not yet enforced — first animation lands in a post-V1 polish PR).

---

## 4. Operating / Process Bestiary

### 4.1 Read-First's first job is "does this already exist?"

Before asking "what's the schema shape?", ask "is this already built?" A brief is operator memory of intent, not a ground-truth source. The codebase wins on disputes.

If the brief proposes a schema and a richer schema already exists in the codebase, honor the existing schema. The brief's wording reflected a clean-slate design assumption that didn't match reality.

**Discovered in:** PR #52 (Contacts CRUD — brief assumed `email` and `phone` columns; PR #39 had already shipped `email_primary` / `email_secondary` and intentionally omitted `phone`).

### 4.2 Adapter dispatch lives in three places — flagged with TODO

Every new ATS adapter (Workday, iCIMS) requires edits in three sites: `_INGESTABLE_ATS` whitelist, `_SUPPORTED` validator, CLI validator. The copy-paste cost compounds with each adapter. Flagged with `TODO(adapter-dispatch-drift)` for a future registry refactor.

**Discovered in:** PR #55 (iCIMS adapter — second adapter exposed the drift).

**Rule:** don't refactor on the second instance. Note it. Refactor on the third or fourth, when the shared shape is obvious.

### 4.3 Tooling false-positives are noted, not silenced

Vercel-related lint or skill warnings firing on backend files (FastAPI on Railway) are known artifacts of a mixed-stack monorepo. Ignored without ceremony. Do not disable the tooling — it's correct for the frontend half. Just don't act on its backend hits.

**Discovered in:** several PRs; first formalized in PR #48.

### 4.4 Operator-side tasks stay with the operator

Some tasks cannot be delegated to Claude Code, regardless of how repetitive they feel:

- Production curl loops against Railway (no outbound network from sandbox; no auth context)
- Vercel/Railway env var changes
- OAuth bootstrap flows (require physical browser consent click)
- Git pushes to main
- Downloading credentials from third-party consoles

Claude Code can write scripts that make these tasks faster (e.g. `refresh_gmail_oauth.py`), but cannot execute them.

**Discovered in:** repeatedly across the session — score sweep, Gmail OAuth refresh, Railway env var updates.

**Rule:** Claude Code writes the script. Operator runs it.

### 4.5 Gmail OAuth refresh tokens expire weekly under Testing status

Google revokes refresh tokens after 7 days when the OAuth client is in Testing publishing status (the default for unverified apps). The Gmail poll cron fails with `RefreshError: invalid_grant` on the next scheduled tick. Fix path: local OAuth bootstrap → copy new refresh token into Railway `GMAIL_REFRESH_TOKEN` env var → cron self-heals on next 15-min tick.

Two paths:

- **(a)** Accept weekly refresh as operator ops cost. Calendar reminder for ~Day 6.
- **(b)** Invest in Google's OAuth verification flow to move client to Production status. Multiple weeks of back-and-forth with Google's review team.

Path (a) is the working answer until the app has many users.

**Discovered in:** PR #56 deploy + first 7-day cycle.

**Operator script:** `apps/api/scripts/refresh_gmail_oauth.py` (committed; runs `InstalledAppFlow` against `apps/api/credentials/google_oauth_client.json`).

### 4.6 Diagnostic instrumentation pays compound interest

When a recurring failure mode is known (Gmail OAuth weekly revocation, classifier prompt drift, etc.), invest in instrumentation BEFORE the failure surfaces, not after. The PR #56 patch wrapping `/admin/gmail/*` 500s in structured `{exc_type, exc_message, hint}` saved a Railway-log spelunking session on the first refresh-token revocation.

Pattern to copy to other cron workflows: structured error body with `exc_type` + `hint` + workflow that pretty-prints the body on failure.

**Discovered in:** PR #56 deploy.

### 4.7 Strip philosophy

When UI / data shape mismatches the API or the current sprint, strip the feature from v1 rather than ship lying UI. Applied across: Companies notes, Add Company, Stats source-effectiveness panel, hard-rule live preview, closed channels editing, Outreach page.

**Rule:** if it's half-built or ambiguous, strip it. Add back when it's earned.

### 4.8 Two pass attempts before declaring cause-4

When a candidate bug has 3 hypothesized causes, run the diagnostic for all 3 before accepting "it's cause 4, something unknown." Cause 4 is real and reasonable, but only after the first three are decisively ruled out via production probes — not by code inspection alone.

**Discovered in:** PR #58 Vanta pass-action bug (initial diagnosis said cause-4-unknown after code inspection; production POST probe immediately revealed cause-3-equivalent: field-name mismatch between frontend and backend).

### 4.9 Production probes are decisive when code inspection alone is ambiguous

A 30-second `Invoke-RestMethod` against the production API can resolve in seconds what code review can't resolve in an hour. When the bug is "shape of bytes on the wire" (payload field names, error response bodies, HTTP status codes), only a real production probe sees the actual bytes. Code inspection sees only the source — and the source is downstream of build steps, environment-specific defaults, and library quirks.

Specifically, when Claude Code reports "Read-First audit shows none of the three candidate causes fire under normal operation," that's a signal to run a production probe before accepting cause-4. The audit verified the code paths; only the probe verifies the actual deployed behavior.

**Discovered in:** PR #58 Vanta pass-action bug (code inspection confirmed reason vocabulary matched, CHECK constraints fired correctly, frontend dispatch always passed `reason`. Production probe POSTing `{kind, reason}` immediately returned 422 with `Field required: action_type` — the bug was field-name divergence between frontend and backend, invisible from either side's source code in isolation).

**Rule:** when code inspection rules out the obvious causes, the next diagnostic is a probe, not a theory.

### 4.10 PR title can lag PR scope — verify code against final agreed direction

Long-running PRs may go through multiple iterations of scope where the title (auto-set from the initial prompt) doesn't reflect what actually shipped. When verifying a merged PR's fix, look at the diff, not the title.

PR #58 was titled "per-company cap + transient retry for Pass action." The transient-retry part referenced an early (incorrect) diagnosis of cold-start failures. The real fix (`kind` → `action_type` field-name mapping) landed correctly in the diff, but the title was never updated, leading to a false-alarm panic that the wrong fix had shipped.

**Discovered in:** PR #58 review.

**Rule:** when checking whether a fix shipped, open the diff. The title is a clue, not a contract.

---

## 5. Test Infrastructure Bestiary

### 5.1 NullPool on the app's module-level engine

Async tests each run in their own event loop. The app's database connection, created once at module load, is bound to whatever loop happened to be active at the time. When the next test gets a different loop, the connection is effectively orphaned and queries hang or fail.

Fix: tell SQLAlchemy to use `NullPool` so every test gets a fresh connection. The fix must be applied to the app's module-level engine, not just the test fixture's engine — the previous four fix attempts patched test fixtures and missed the production engine bound to the wrong loop.

**Discovered in:** PR #48 (classifier improvement — five fix commits to get green; the actually-effective fix patched the app's module-level engine).

### 5.2 `cast(col, Text)` not `cast(col, text("text"))`

`TextClause` (from `text("text")`) is not a `TypeEngine`. SQLAlchemy raises a confusing error if you pass it where a column type is expected.

Right: `cast(JobPosting.target_company_id, Text)`.
Wrong: `cast(JobPosting.target_company_id, text("text"))`.

**Discovered in:** PR #48.

### 5.3 `asyncio_default_fixture_loop_scope = "session"` in pytest config

When async fixtures share state, set the fixture loop scope to `"session"` in `pyproject.toml` / `pytest.ini`. Per-function scope (the default) is the source of obscure "the test that ran 30s ago left state visible" failures.

**Discovered in:** PR #48.

### 5.4 Real LLM calls prohibited in CI

Every test that touches a service calling an LLM (classifier, scoring, JD summary) uses mocked clients. Real Gemini calls in CI are explicitly prohibited — both for cost reasons and because they introduce non-determinism into the test suite.

Standard mock seam: top-level callable (e.g. `classify_posting()`, `score_posting()`) monkeypatched via `monkeypatch.setattr("job_assist.services.<module>.<callable>", stub)`.

**Discovered in:** PR #48, formalized in PR #56.

### 5.5 asyncpg + connection-rotating pooler = `statement_cache_size=0`

Any deployment that pairs asyncpg with a pooler that rotates the backend connection between transactions (Supabase Pooler in Transaction mode, PgBouncer Transaction mode, etc.) requires `statement_cache_size=0` on the engine. asyncpg's default cache size is 100; cached prepared statements collide on recycled connections, raising `DuplicatePreparedStatementError` on the next request that lands on a rotated connection.

SQLAlchemy silently passes through `connect_args` and provides no warning when the default is unsafe under the deployment's pooler mode.

Fix:

```python
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"statement_cache_size": 0},
)
```

Local Postgres has no pooler in the path, so this bug is invisible until production. CI passes; production fails. The bug is latent on any query — the more dynamic the SQL shape, the more frequently it surfaces. PR #58's CTE made the bug fire on virtually every `/postings` call, but the Gmail poll cron hit it on a simple watermark SELECT with no recent code change.

The trade-off of disabling the cache is microscopic — slight query parsing overhead per request, completely unnoticeable at single-operator scale.

**Discovered in:** PR #58 hotfix (per-company cap CTE on `/postings`) and PR #58 follow-up engine-config fix (Gmail poll cron).

**Rule:** any new async engine setup gets `statement_cache_size=0` in `connect_args` by default. Verify on every PR that touches `db/session.py` or equivalent.

---

### 5.6 Ruff's en-dash flags scope by file location

`RUF002` fires on ambiguous Unicode dashes (`–`, U+2013) in docstrings and string literals; `RUF003` fires on the same characters in comments. Both are easy to trigger because the en-dash reflex for number ranges (e.g., `200–800`) feels natural in prose but lights up Ruff.

The trap: locally running `ruff check src tests` skips `migrations/` and any other directory not explicitly named. CI runs `ruff check .` from `apps/api`, which catches everything including migration files. A local lint pass can be "clean" while CI fails on the same code.

Three fixes, pick one:
1. **Match CI scope locally:** always run `ruff check .` from `apps/api`, never narrow to `src tests`.
2. **Stick to ASCII hyphens** in Python files. `200-800` is fine; `–` is a footgun.
3. **Pre-commit hook** that runs `ruff check .` and blocks the commit on any failure. Higher friction but catches the bug before push.

Pick 1 + 2. Pre-commit is overkill for a single-operator repo.

**Discovered in:** PR #59 — en-dash in `config.py` comment, then again in the migration docstring on the same PR. Two strikes in one session.

---

### 5.7 OpenAPI snapshot regen must match CI's exact command including flags

The drift-check compares the committed `apps/api/openapi.json` byte-for-byte against `app.openapi()` generated under CI's exact invocation. Small differences in regeneration flags produce snapshot diffs that fail CI even when the schema is semantically identical.

Specifically: `json.dumps(..., sort_keys=True)` produces a different byte sequence than `json.dumps(...)` without `sort_keys`. If CI regenerates one way and the operator regenerates the other way, every PR touching the schema will fail the drift check.

Mitigation: ship `apps/api/scripts/regen_openapi.py` that exactly mirrors the CI regeneration logic — same `sort_keys` setting, same indent, same newline behavior, same encoding. Reference the script from any PR that touches the OpenAPI surface.

Open question worth a future small PR: lock CI's regeneration into a single shared helper imported by both the snapshot writer and the drift-check reader, so the two paths can't diverge.

**Discovered in:** PR #59 — initial OpenAPI regen used `sort_keys=True`, CI's command did not; drift-check failed until the regen flag matched CI.

---

### 5.8 Migration UPDATE guards on the OLD value can no-op silently

When a schema migration includes both `op.alter_column(server_default=NEW)` and `op.execute("UPDATE table SET col = NEW WHERE col = OLD")`, the UPDATE is intentionally guarded on the old default value. This pattern correctly preserves operator-customized values across deploys — if the operator already changed the value, the WHERE clause skips that row.

The side effect: if any operator already manually set the value to NEW before the migration shipped, the UPDATE matches zero rows and runs as a true no-op. The schema default updates but no `updated_at` ticks on any existing row.

To verify a migration actually ran the UPDATE you intended (vs. running but no-op'ing), check both:
- **Schema-level effect:** column default changed (visible in `pg_attribute` or via SQLAlchemy reflection).
- **Data-level effect:** at least one row's `updated_at` advanced past the migration timestamp.

If the data-level signal is absent but the schema-level signal is present, the migration ran but updated zero rows. That's usually benign (the row already had the new value), but worth confirming the production state matches expectations before assuming the migration "worked."

**Discovered in:** PR #59 (applicant_cap 150 → 500). The live `operator_profile.updated_at` predated the migration merge by 3 days; investigation confirmed an earlier manual PUT had set the value to 500, and the migration's `WHERE applicant_cap = 150` guard correctly skipped the row. Both schema and data ended up in the desired state via two independent paths, but the data path wasn't the migration.

---

### 5.9 Silent 404 swallow in ATS adapters masks operator-actionable failures

ATS adapters that return `[]` on any non-200 response conflate three different upstream states into one observable outcome:

1. *Tenant has no postings right now* — legitimate empty.
2. *Tenant migrated off this ATS* — stale config, operator-actionable.
3. *Configured ats_handle is wrong* — typo or rebrand, operator-actionable.

All three surface to the operator identically: `postings_fetched=0`, `status="success"`, no warning. The operational consequence: `target_company` rows can carry stale ATS handles for months without anyone noticing. Discovered in PR #63 follow-up when Plaid (`lever/plaid`) returned `fetched=0` — Plaid had migrated to Ashby and the seed config was stale, but the ingest cron silently kept reporting success.

**Fix:**
- Each adapter raises `HandleNotFoundError` on 404 specifically for its *listing-level* call. Mid-pagination 404 (per-job-detail or page-2+) stays silent — those are not handle-level failures.
- The orchestrator catches `HandleNotFoundError` and sets `IngestRun.status = "handle_not_found"`, distinct from generic `failed` (which still covers network errors, parsing failures, scoring exceptions, etc.).
- Adding the enum value uses Alembic's `autocommit_block` pattern (see 2.6) since Postgres can't `ALTER TYPE ... ADD VALUE` inside a transaction.

**Discovered in:** PR #63 follow-up — lever/plaid + lever/atlassian zero-fetch investigation, 2026-05-26. Plaid was on Ashby (not Lever); Atlassian's actual ATS couldn't be determined from public probes and was soft-paused with `ats_handle = NULL`.

---

### 5.10 CI failure screenshots can show stale runs after a force-push

GitHub Actions workflow runs are immutable. When a branch gets force-pushed (e.g., after a rebase), the old workflow runs against the pre-push SHAs don't disappear — they sit in run history with their original failure status. A screenshot of a CI failure with a `/runs/<id>` URL pins to that specific historical run, not to the latest run on the branch.

This creates a debugging trap when the operator pastes a screenshot mid-investigation:

- The screenshot shows "CI failing"
- The strategist tries to diagnose the failure
- But the latest run (on the post-rebase HEAD) actually passed

Fix: before diagnosing any "still failing" CI screenshot, confirm the run's `headSha` matches the branch's current HEAD:

```bash
gh run list --branch <branch> --json headSha,conclusion,url --limit 5
git rev-parse HEAD
```

If the screenshot's SHA doesn't match HEAD, the screenshot is stale. Get fresh status via `gh pr checks <pr-num>` instead.

**Discovered in:** PR #65 (Plaid + Atlassian data fix). The migration-check CI step appeared to fail in a screenshot, but the failure was pinned to a pre-rebase commit. The post-rebase run on the actual branch HEAD was green.

### 5.11 Frontend hardcoded limit > API cap silently shows empty state

When a frontend hook requests `?limit=500` but the API caps at 100 and returns 422, the typical React Query error fallthrough renders the empty state instead of an error state. The visible symptom: 'No data' when data actually exists.

Two-part fix:
1. Match frontend limits to API caps (or page below the cap).
2. Surface API errors explicitly at the page level — never let a 422 collapse silently to an empty state. The error card needs to be a deliberate render path, not a missing else-branch.

**Discovered in:** PR #66 follow-up audit (2026-05-26). The /passed page silently rendered 'No passed postings yet' while the API had 4 not_interested rows; the 422 from limit=500 was swallowed by React Query's empty default. Same pattern at /applied, /stats, /rejected.

### 5.12 React Query cache keys must not be shared across hooks returning different shapes

Multiple hooks using the same cache key prefix (e.g., `['postings', ...]`) with different cached value shapes will collide when any mutation iterates `qc.getQueriesData({queryKey: [prefix]})`. The iteration returns entries of mixed shape, and code that assumes one shape will crash on entries of another.

Specifically: `useSavedFilterCount` stored a `number` under `['postings', ...]` while `useTriagePostings` / `usePassedPostings` / `useRejectedPostings` / `useAppliedPostings` stored `PostingsListResponse` under the same prefix. `useRecordAction.onMutate` iterated all entries and crashed on the numeric ones with TypeError on `prev.items.filter`. The crash happened synchronously inside onMutate, so mutationFn never ran and no network request fired.

Symptoms when this bug class is present:
- "Mutation appears to fail" — toast fires, no network activity
- Mutation lifecycle aborts in onMutate / onSettled / onSuccess without explanation
- Bug is invisible in unit tests that seed the cache with only one shape

Two-part fix:
1. Distinct cache keys per shape (`['postings-count', ...]` separate from `['postings', ...]`)
2. Defense-in-depth shape guards in any code that iterates a multi-shape cache prefix

**Discovered in:** PR #68 (Pass-action handler crash investigation). The bug had been latent since PR #32b (when useSavedFilterCount was introduced sharing the key) but only became operationally visible after PR #58 fixed the wire shape — until then, the wire-shape bug masked the cache-collision bug.

### 5.13 Pagination on the most-used list page is operator-critical, not optional

Triage shipped originally with hardcoded 20-row limit and no pagination. The audit found this only after the corpus grew past 50 rows. Pre-corpus-growth, the test scenario "operator sees full list" passed; post-growth, the operator silently couldn't reach 96% of postings.

Same lesson as PR #67's frontend audit findings (5.11) — list pages with growable data must have pagination from PR 1, not added retroactively when the data grows. The same Load More pattern from OutreachTimeline → PR #67 → this PR is the canonical answer until the 2-page ceiling (issue #68) becomes operator-visible.

**Discovered in:** PR #69 follow-up (frontend audit, 2026-05-26). 696 of 716 pending postings were unreachable from the UI.

---

## 6. Privacy / Safety Bestiary

### 6.1 xlsx files containing real PII never committed

Source files (Tippie alumni xlsx, LinkedIn exports, recruiter data) must NEVER be committed. `.gitignore` enforces this before any work that touches such a file:

```
*.xlsx
*alumni*.xlsx
/mnt/user-data/uploads/*
apps/api/credentials/
```

Verify with `git status` before every commit that touches PII-adjacent code.

**Discovered in:** PR #51 (Tippie alumni seed).

### 6.2 Ingestion code enforces opted-in filter at the parser

Filtering by `opted_in=True` must be enforced in code, not as a comment. The line `if not row.opted_in: continue` lives in the parser before any row reaches the writer. Comments saying "remember to filter" are not sufficient.

**Discovered in:** PR #51.

### 6.3 Test fixtures use fake names only

No real PII appears in any test fixture, log line, or chat-visible output. Test data uses obviously-fake names like `Test Person 1`, `test1@example.com`, `Alpha Co`, etc.

The xlsx parser logs row counts only — never individual row contents. The API endpoint never returns full contact lists in a single response; paginated with `limit` capped at 100.

**Discovered in:** PR #51.

### 6.4 Logs never include contact PII

No log line — backend or frontend — includes names, emails, phone numbers, or LinkedIn URLs of real people. Logging is for counts, IDs (UUIDs), and event types. Anything else is a privacy leak.

**Discovered in:** PR #51.

### 6.5 OAuth client secrets are not refresh tokens

Two different credentials with different sensitivity:

- **OAuth client secret** (in `apps/api/credentials/google_oauth_client.json`): identifies the *app* to Google. Sensitive but not user-tied. Gitignored.
- **Refresh token** (in Railway `GMAIL_REFRESH_TOKEN` env var): identifies *the user's grant of access*. More sensitive — never in repo, never in chat, only in Railway secrets storage.

Conflating them leads to either (a) committing the wrong file thinking it's safe or (b) reissuing the wrong credential when the cron fails.

**Discovered in:** PR #56 + first 7-day Gmail OAuth refresh cycle.

### 6.6 Gitignore directory patterns vs file patterns are not interchangeable

`credentials.json` in `.gitignore` matches FILES literally named `credentials.json`. It does NOT match files inside a DIRECTORY named `credentials/`. The rules are distinct:

```
# Matches a file named credentials.json at any depth
credentials.json

# Matches every file inside apps/api/credentials/ at any depth
apps/api/credentials/

# Matches every file inside any directory named credentials/
**/credentials/
```

If the credential file lives at `apps/api/credentials/google_oauth_client.json`, the gitignore needs the directory pattern, not the file pattern.

Verify after editing `.gitignore`:

```powershell
git check-ignore -v <path-to-secret-file>
```

Should print the matching rule. If it prints nothing, the file is NOT ignored.

**Discovered in:** Bestiary commit session (`.gitignore` had `apps/api/credentials.json` and `credentials.json` as file-name rules; the OAuth client secret at `apps/api/credentials/google_oauth_client.json` was untracked but NOT ignored — one accidental `git add .` away from a public GitHub commit).

**Rule:** after creating any new credentials directory, immediately add the directory pattern (with trailing slash) to `.gitignore` AND verify with `git check-ignore` before any `git add` runs.

---

## Maintenance

When you add an entry:

1. Pick the right section. Create a new section only if no existing one fits — most entries land in 1-6.
2. Use the format: `### N.X Title (short)`, then problem, lesson, and `**Discovered in:** PR #NN`.
3. Add a code example only when the prose isn't precise enough on its own.
4. Cross-reference related entries with `(see N.X)` rather than restating.
5. Keep entries tight. This is reference material, not narrative.

Entries from before this document existed (PR #1-47) live only in chat history and commit messages. They'll get backfilled if they cause new bugs.
