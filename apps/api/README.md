# Job Assist API

FastAPI backend for Job Assist. Owns ingestion, triage, Gmail, RAG.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd apps/api
uv sync
cp ../../.env.example .env  # fill in values
uv run uvicorn job_assist.main:app --reload
```

API runs at `http://localhost:8000`. OpenAPI docs at `/docs`.

## Commands

```bash
uv run pytest                    # tests
uv run ruff check .              # lint
uv run ruff format .             # format
uv run mypy src                  # typecheck
```

## Structure

```
src/job_assist/
├── main.py              # FastAPI app, lifespan, health
├── config.py            # Settings from .env
├── db/                  # SQLAlchemy models, session, migrations (added Week 1)
├── adapters/            # Greenhouse, Lever, Ashby (added Week 1)
├── triage/              # Hard rules, embeddings, LLM verdicts (added Week 3)
├── gmail/               # OAuth, backfill, monitoring (added Week 2)
├── rag/                 # Embeddings, retrieval, synthesis (added Week 5)
└── tailor/              # Outreach drafts (added Week 5)
```
