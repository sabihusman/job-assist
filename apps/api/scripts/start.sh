#!/usr/bin/env bash
# Layer 1 of the migration-deploy gate (feat/migration-deploy-gate).
#
# Runs the migration and the server as ONE atomic unit: `alembic upgrade head`
# THEN `exec uvicorn`. With `set -euo pipefail`, a failed migration exits the
# script non-zero, so the container never starts uvicorn — the platform keeps
# the prior healthy deployment serving. This closes the hole behind #104/#107,
# where code shipped ahead of its schema and served 500s.
#
# Uses the RUNTIME DATABASE_URL from the environment (Railway service env /
# Docker env), NOT a CI secret — so it can't fail on a stale GitHub secret.
#
# Tools run via `uv run` because this is a uv-managed project: the deps live in
# uv's venv, which is NOT on bare PATH in the Railway/Nixpacks container (the
# prior working start command was `uv run uvicorn …`). `uv run` resolves
# alembic/uvicorn from that venv; bare invocations would fail "command not
# found". Host-agnostic: invoked via the Procfile on Railway (Nixpacks) and as
# the Dockerfile ENTRYPOINT on Hetzner later — the same script, unchanged.
set -euo pipefail

# Resolve to apps/api regardless of the caller's cwd (scripts/ lives under it),
# so pyproject.toml/uv.lock, alembic.ini, and the package import path are found
# on any host.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

echo "[start.sh] Applying migrations (uv run alembic upgrade head)…"
uv run alembic upgrade head
echo "[start.sh] Migrations at head. Starting uvicorn…"

# Binds Railway's $PORT (8000 is only a local fallback).
exec uv run uvicorn job_assist.main:app --host 0.0.0.0 --port "${PORT:-8000}"
