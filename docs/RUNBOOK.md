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

## Troubleshooting

### CI fails on `pnpm-lock.yaml not found`

Run `pnpm install` locally and commit the lockfile.

### Playwright fails in CI but passes locally

Check that browsers were installed (`playwright install --with-deps chromium`). Verify the web server is fully started before tests run.

### Gmail OAuth fails with "redirect_uri_mismatch"

Verify Google Cloud Console → Credentials → OAuth client → Authorized redirect URIs includes `http://localhost:8000/oauth/gmail/callback`.

## Public repo data discipline

This repo is public. The following rules prevent personal job-search data from
leaking into the commit history.

### What must never be committed

| Data | Why |
|---|---|
| Real company names tied to rejection state | Personally identifying; reputationally sensitive |
| Gmail credential JSON or OAuth token files | Full account access |
| Migration files that embed rejection patterns (e.g. seeded `outcome_event` rows) | Contains application history |
| `apps/api/seeds/seeds.json` | Contains real target companies and rejection flags |

`.gitignore` covers `apps/api/seeds/seeds.json`, `*.gmail-token`, and
`credentials.json`. If you accidentally stage one of these, run
`git rm --cached <file>` before committing.

### Seed data

Real seed data (target companies, triage rules, rejection patterns) lives in
`apps/api/seeds/seeds.json` and is **gitignored**.

A committed `apps/api/seeds/seeds.example.json` contains synthetic placeholder
data so contributors can understand the schema without seeing real data:

```json
{
  "target_companies": [
    { "name": "Acme Wealthtech", "ats": "greenhouse", "tier": 1 },
    { "name": "Example Fintech", "ats": "lever",      "tier": 2 }
  ],
  "hard_rule_overrides": [
    { "company": "Example Corp", "rule": "non-pm-only", "active": true }
  ]
}
```

To load your real seeds locally:

```bash
cd apps/api
cp seeds/seeds.example.json seeds/seeds.json
# Edit seeds/seeds.json with real data — this file is gitignored
uv run python -m job_assist.cli seed
```

### Application state and outcome data

`application_state` and `outcome_event` rows live in Supabase only. They are
never exported to fixtures, factory files, or any file tracked by git. If a
test needs application-state data, generate it synthetically in the test itself:

```python
# Good — synthetic, never touches real rejection history
app = ApplicationStateFactory(company="Fake Corp", state="rejected")

# Bad — loading real outcome_event rows from a fixture file
```

### Test fixtures and E2E data

Gmail classifier tests use synthetic email bodies, not real rejection emails.
No fixture file in `apps/api/tests/` may contain a real company name paired
with a rejection or non-response signal.

---

### Adapter returns 0 results

Hit the endpoint manually with `curl` first. Most likely causes:
- Company not on this ATS — verify board URL in browser
- Company changed their board slug — update `target_company.source_handle`
- ATS rate limit — backoff and retry
