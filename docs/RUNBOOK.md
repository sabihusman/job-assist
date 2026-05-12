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

### Adapter returns 0 results

Hit the endpoint manually with `curl` first. Most likely causes:
- Company not on this ATS — verify board URL in browser
- Company changed their board slug — update `target_company.source_handle`
- ATS rate limit — backoff and retry
