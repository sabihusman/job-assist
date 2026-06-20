"""A4 eval pipeline — OFFLINE ONLY (Phase 1: OpenAI independent pre-labeler).

Hard isolation invariant (enforced by ``tests/eval/test_isolation.py``):
  * ``openai`` is imported ONLY inside this package — never by any production
    module under ``job_assist`` (cron, sweep, ingest, scoring, route, gmail).
  * No production module imports ``job_assist.eval`` — nothing here is wired
    into a FastAPI route or a cron.

Everything in this package runs offline (a CLI / a manual ``workflow_dispatch``),
never in a request/cron/sweep/hot path. OpenAI is the *pre-labeler* (independent
ground-truth proposer); Gemini remains the only production model and the model
under test. ``OPENAI_API_KEY`` is read from the environment at call time and is
a Railway-env secret — never committed.
"""
