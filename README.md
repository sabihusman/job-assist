# Job Assist

Personal job-search aggregation and triage system. Pulls postings from multiple ATS sources, scores them against a profile and rule set, and surfaces a daily digest of best matches. No automated applying.

## Architecture

Monorepo with two deployable apps and shared types.


```
job-assist/
├── apps/
│   ├── api/                    # Python FastAPI backend
│   │   ├── src/job_assist/     # Ingestion, triage, Gmail, RAG, LLM work
│   │   └── tests/              # pytest
│   └── web/                    # Next.js frontend
│       ├── src/app/            # Routes: feedback UI, Q&A, digest preview
│       └── public/
├── packages/
│   └── shared-types/           # TS types generated from FastAPI OpenAPI
├── .github/workflows/          # CI/CD
└── docs/                       # Architecture decisions, runbook
```

**Backend (`apps/api`)** — Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, httpx, pgvector. Owns all data ingestion (Greenhouse, Lever, Ashby adapters), triage (hard rules + embeddings + LLM verdicts), Gmail integration, RAG Q&A.

**Frontend (`apps/web`)** — Next.js 15, TypeScript, Tailwind, shadcn/ui. Auth, feedback UI (Interested / Not interested / Applied / Snooze), Q&A interface, digest preview. Calls the API via typed client.

**Database** — Supabase Postgres + pgvector. Both apps connect.

**Deployment** — API on Railway (or Fly.io). Web on Vercel.

## Status

🚧 Bootstrap. See [`docs/DECISIONS.md`](./docs/DECISIONS.md) for architecture decisions and [`docs/ROADMAP.md`](./docs/ROADMAP.md) for build phases.

## Development

Prerequisites: Node.js 20+, pnpm 9+, Python 3.12+, uv.

```bash
# Web
cd apps/web
pnpm install
pnpm dev

# API
cd apps/api
uv sync
uv run uvicorn job_assist.main:app --reload
```

## Principles

- No automated applying. The system drafts and triages; the human applies.
- Code handles unattended work (ingestion, scoring, digest). Human handles judgment (decisions, applications).
- Hard rules filter before LLM/embedding scoring runs. Cost discipline.
- All changes via feature branches and PRs. `main` is protected.
