# Job Assist — working conventions

Monorepo: `apps/api` (Python 3.12 / FastAPI / SQLAlchemy / Alembic, managed with uv + `.venv`) and `apps/web` (Next.js, pnpm). Prod API runs on Railway: `api-production-ca5ad.up.railway.app`.

## Python (apps/api)
- Always invoke tools through the venv: `./.venv/Scripts/python -m pytest`, `./.venv/Scripts/python -m ruff check src tests`, `./.venv/Scripts/python -m ruff format --check src tests`.
- `uv` needs `--native-tls` on this machine.
- Known local quirk: multi-file async pytest runs can abort locally (environment issue, NOT a test failure). If a broad run aborts, re-run in smaller per-directory batches before concluding anything failed.

## OpenAPI artifacts
- `apps/api/openapi.json` is committed and must end with a trailing newline; the CI snapshot gate is byte-exact. Regenerate after any route/schema change:
  `./.venv/Scripts/python -c "import json; from job_assist.main import app; open('openapi.json','wb').write(json.dumps(app.openapi(), indent=2).encode() + b'\n')"` (run from `apps/api`).
- The generated web client `.ts` is gitignored — never commit it.

## Web (apps/web)
- `pnpm-workspace.yaml` must list `apps/web` explicitly. Never widen to `apps/*` — `apps/api` is Python and must not become a pnpm workspace member.
- Checks: `pnpm typecheck`, `pnpm lint`, `pnpm vitest`.

## Git
- `main` is unprotected; PRs merge via the merge-train script (Git Bash `/tmp/mt.sh`).
- Before every commit, verify the staged set with `git diff --cached --stat` — `git add` can fail silently here and produce empty PRs.
- The repo is PUBLIC. Never commit real application/personal data: no `*.xlsx`/`*.csv` eval data, no OAuth credentials (`apps/api/credentials/` is ignored).

## Prod debugging
- If a prod GET looks "stale", check the endpoint's Pydantic `response_model` for field stripping BEFORE blaming Railway caching/replicas.
- Ops workflows (manual dispatch): `ingest-runs-probe` (list failed ingest runs), `deactivate-company` (retire dead handles). See docs/RUNBOOK.md.
