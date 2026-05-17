# Runbook

How to run, debug, and operate the system.

## Local dev

### One-time setup

Install prerequisites:
- Node.js 20+ (use `nvm use` in repo root)
- pnpm 9+ (`npm install -g pnpm`)
- Python 3.12+ (use `pyenv install 3.12` if needed)
- uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Supabase CLI (`brew install supabase/tap/supabase`)

Clone and set up:

```bash
git clone <repo>
cd job-assist
pnpm install
cd apps/api && uv sync && cd ../..
cp .env.example apps/api/.env
cp .env.example apps/web/.env.local
# Fill in values in both .env files
```

### Run the web app

```bash
pnpm dev:web
# → http://localhost:3000
```

### Run the API

```bash
cd apps/api
uv run uvicorn job_assist.main:app --reload
# → http://localhost:8000
# → http://localhost:8000/docs (OpenAPI UI)
```

### Run tests

Web:
```bash
pnpm --filter web test          # unit
pnpm --filter web test:e2e      # e2e (requires dev server running)
```

API:
```bash
cd apps/api
uv run pytest
```

## Regenerating shared TypeScript types

`packages/shared-types/src/api.d.ts` is generated from the FastAPI OpenAPI schema. It is **not** auto-generated in CI — regeneration is a manual step before any PR that changes the API schema.

When to regenerate:
- You add, remove, or change any FastAPI route, request body, or response model.
- Before opening a PR so reviewers see the type diff alongside the API diff.

Steps:

```bash
# 1. Start the API locally (must be running to serve /openapi.json)
cd apps/api
uv run uvicorn job_assist.main:app --reload

# 2. In a separate terminal, regenerate types
pnpm --filter @job-assist/shared-types generate

# 3. Commit the result as part of your feature branch
git add packages/shared-types/src/api.d.ts
git commit -m "chore: regenerate shared types"
```

The generated file is committed to the repo. CI does not regenerate it — if the file is stale, `pnpm --filter web typecheck` will fail on the types mismatch, which is the signal to regenerate.

## Operations

### Branch workflow

1. Branch off `main` — `git checkout -b feat/<short-name>`
2. Commit using conventional commits — `feat:`, `fix:`, `chore:`, `docs:`
3. Push and open PR
4. CI must pass before merge — lint, typecheck, test on both apps + e2e
5. Squash-merge to `main`
6. Vercel and Railway auto-deploy on merge

### Required GitHub settings (set once via UI)

- Settings → Branches → Add rule for `main`:
  - Require status checks to pass before merging
  - Required check: `All Checks`
  - Require branches to be up to date before merging
  - Do not allow bypassing the above settings
  - Restrict who can push to matching branches → only allow via PR

### Adding a new ATS adapter

1. Add to `apps/api/src/job_assist/adapters/<name>.py`
2. Implement the `Adapter` protocol (defined in `adapters/base.py`)
3. Add tests in `apps/api/tests/adapters/test_<name>.py`
4. Register in the adapter registry
5. Add target companies to `target_company` table with `ats=<name>`

### Triggering ingestion manually

```bash
cd apps/api
uv run python -m job_assist.cli ingest --source greenhouse
uv run python -m job_assist.cli ingest --all
```

### Daily ingest cron

The `Daily ingest` GitHub Actions workflow (`.github/workflows/ingest-daily.yml`)
runs once a day and exercises every `(ats, handle)` pair the API advertises
via `GET /admin/ingest/plan`.

| | |
|---|---|
| **Schedule** | `0 6 * * *` UTC (06:00 UTC = midnight US Central) |
| **Per-call throttle** | 5 s gap between ATS hits (override via `THROTTLE_SECONDS` env var on the workflow if ever needed) |
| **Per-call timeout** | 120 s (Anthropic's 411-posting Greenhouse board is the long tail) |
| **Job timeout** | 30 minutes |
| **Failure mode** | Workflow exits non-zero on any failed ingest; GitHub's stock "workflow failed" email fires to repo admins |
| **Concurrency** | `concurrency: daily-ingest` — a second cron firing while one is still running is queued, not cancelled |

**Manual trigger:** Actions tab → **Daily ingest** → "Run workflow" (uses
`workflow_dispatch`). Safe to fire ad-hoc; the ingestion service is
idempotent at the `(source_job_id)` level.

**Pause the cron:** Actions tab → **Daily ingest** → "···" menu → "Disable
workflow". Re-enable when ready; no code change needed.

**Add a new company to the cron:** insert it into `target_company` with a
supported `ats` (greenhouse / lever / ashby) and `ats_handle` populated.
The next scheduled run picks it up without any workflow edit. The seed
file at `apps/api/seeds/target_companies.json` is the canonical source
of truth for the operator's target list — edit it and re-POST to
`/admin/seed/target-companies` to push to prod.

**Why 06:00 UTC?** Most ATS boards refresh during US business hours. A
midnight-Central run lands the prior day's net-new postings before the
operator's morning triage pass.

### Inspecting state

Supabase dashboard → SQL editor:

```sql
-- Recent ingestion runs
SELECT * FROM ingest_run ORDER BY started_at DESC LIMIT 20;

-- Top postings from last 24h
SELECT * FROM job_posting
WHERE first_seen_at > now() - interval '24 hours'
ORDER BY first_seen_at DESC;

-- Companies with rejection patterns
SELECT company, count(*) AS rejections
FROM outcome_event
WHERE outcome_type = 'rejection'
GROUP BY company
HAVING count(*) >= 3
ORDER BY rejections DESC;
```

### Gmail OAuth setup

One-time operator setup to populate the env vars that the
`/admin/gmail/backfill` endpoint requires. Read-only scope only; the app
never sends mail, modifies labels, or persists access tokens to disk.

**1. Create a Google Cloud project.** Reuse an existing one if you have it.

**2. Enable APIs** in the Cloud Console:
- Gmail API
- Generative Language API

**3. Configure the OAuth consent screen:**
- User type: **External**
- Publishing status: **Testing** (the only test user is your own Gmail)
- Scope: `https://www.googleapis.com/auth/gmail.readonly` — nothing else
- Test users: add the Gmail address you want to back-fill (just yours)

**4. Create the OAuth client:**
- Type: **Desktop application** (simplest for the one-off offline flow)
- Download the JSON. This is what becomes `GMAIL_CREDENTIALS_JSON`.

**5. Get a refresh token via a one-time local run.** From a Python shell
on your machine (the package `google-auth-oauthlib` is already in
`apps/api/pyproject.toml`):

```python
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
print(creds.refresh_token)
```

This opens a browser, you consent once, and the script prints the
refresh token. **Copy it.**

**6. Get a Gemini API key.** Free tier at
[aistudio.google.com](https://aistudio.google.com/apikey).

**7. Upload all three secrets to Railway** (Service → Variables):

| Var | Value |
|---|---|
| `GMAIL_CREDENTIALS_JSON` | Paste the entire contents of `credentials.json` as a multi-line string |
| `GMAIL_REFRESH_TOKEN` | The refresh token from step 5 |
| `GEMINI_API_KEY` | The Gemini key from step 6 |

Railway redeploys automatically. Verify with:

```bash
curl -s https://api-production-ca5ad.up.railway.app/health
```

Then trigger the backfill:

```bash
curl -X POST 'https://api-production-ca5ad.up.railway.app/admin/gmail/backfill?days=60'
```

The request blocks for 5–10 minutes (Gemini free tier 15 RPM throttle).
The response is the full `BackfillReport` counters.

**Refresh token rotation.** The refresh token does not expire as long as
the OAuth client stays in *Testing* status and you remain a test user.
If the token ever gets revoked (e.g. you remove the OAuth client),
re-run step 5 and update `GMAIL_REFRESH_TOKEN` on Railway.

**Never commit:**
- `credentials.json`
- The refresh token
- Any cached token file

`.gitignore` already covers these (`apps/api/credentials.json`,
`*credentials*.json`, `*.gmail-token`).

## Troubleshooting

### CI fails on `pnpm-lock.yaml not found`

Run `pnpm install` locally and commit the lockfile.

### Playwright fails in CI but passes locally

Check that browsers were installed (`playwright install --with-deps chromium`). Verify the web server is fully started before tests run.

### Gmail OAuth fails with "redirect_uri_mismatch"

With the Desktop OAuth client described in "Gmail OAuth setup", the
redirect URI is automatically `http://localhost:<random-port>/` — set
by `run_local_server(port=0)`. If you see a redirect-URI error, you're
likely using a *Web application* OAuth client by mistake. Re-create the
client as **Desktop application** type.

### Gmail backfill returns 503 "missing env var(s)"

The `/admin/gmail/backfill` endpoint requires `GMAIL_CREDENTIALS_JSON`,
`GMAIL_REFRESH_TOKEN`, and `GEMINI_API_KEY`. Set them on Railway and
trigger a redeploy. See "Gmail OAuth setup" above.

## Public repo data discipline

This repo is public. The rules below prevent personal job-search data from
leaking into the commit history. The principle is simple: **schema goes in
the repo, data goes in private files or the runtime database, and the
public test fixtures use synthetic values only**.

### Never commit

- Real company names tied to rejection state or outcome events.
- Real email addresses (yours or anyone else's) outside synthetic test fixtures.
- Real Gmail message IDs or thread IDs.
- Real outcome classifications (e.g., `outcome_event` rows pointing to real companies).
- OAuth tokens, credentials JSON files, API keys.
- Database passwords or connection strings with real passwords.

### Always commit

- Architectural concepts and patterns.
- Synthetic test fixtures (`test@example.com`, `ExampleCo`, `Acmecorp`, ...).
- Migration schema (DDL only — no seed data).
- `.example.json` files showing schema shape.

### Tables that must never appear with real data

- `outcome_event`
- `application_state`
- `closed_channel`
- `triage_result` (once populated with LLM verdicts)

### Tables that may only ever appear in migrations as schema

- `target_company` — real data lives in `apps/api/seeds/target_companies.json`
  (gitignored) and is loaded via `python -m job_assist.seed` (locally) or
  `POST /admin/seed/target-companies` (production).

### Gitignored paths

- `apps/api/seeds/*.json` (except `*.example.json`)
- `apps/api/.env`
- `apps/api/credentials.json`, `apps/api/token.json`, any `*credentials*.json`, any `*token*.json`
- `.vercel/`
- `.claude/`

If you accidentally stage one of these, run `git rm --cached <file>` before
committing.

### Seeding `target_company`

Real target-company data lives in `apps/api/seeds/target_companies.json`
(gitignored). The committed `apps/api/seeds/target_companies.example.json`
is the public schema template.

**Local first-time setup:**

```bash
cd apps/api
cp seeds/target_companies.example.json seeds/target_companies.json
# Edit seeds/target_companies.json with the real target list
uv run python -m job_assist.seed
```

**Production:** the JSON travels as the POST body so the file never lands
on the Railway container:

```bash
curl -X POST -H 'Content-Type: application/json' \
     -d @apps/api/seeds/target_companies.json \
     https://api-production-ca5ad.up.railway.app/admin/seed/target-companies
```

Both paths are idempotent — rows are matched by `name` and existing rows
are left alone (skipped), so re-running is safe.

### Application state and outcome data

`application_state` and `outcome_event` rows live in Supabase only. They are
never exported to fixtures, factory files, or any file tracked by git. If a
test needs application-state data, generate it synthetically in the test:

```python
# Good — synthetic, never touches real rejection history
app = ApplicationStateFactory(company="Fake Corp", state="rejected")

# Bad — loading real outcome_event rows from a fixture file
```

### Test fixtures and E2E data

Gmail classifier tests use synthetic email bodies, not real rejection emails.
No fixture file in `apps/api/tests/` may contain a real company name paired
with a rejection or non-response signal. Handle-generation tests use
synthetic names (`Acmecorp`, `Acme Insurance Cross Shield Group`) that
preserve the input shape without identifying real targets.

---

### Adapter returns 0 results

Hit the endpoint manually with `curl` first. Most likely causes:
- Company not on this ATS — verify board URL in browser
- Company changed their board slug — update `target_company.source_handle`
- ATS rate limit — backoff and retry
