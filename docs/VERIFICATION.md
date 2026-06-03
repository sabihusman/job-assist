# Verification Standard

The working standard for this repo. It applies to **every feature, old or new**.
It exists because this codebase has repeatedly shipped features that were
"built" and "CI green" but did nothing, claimed effects they didn't have, or
took production down. Each rule cites the real incident it came from.

---

## 1. "Built" / "CI green" ≠ "working"

A feature is **DONE only when it is verified doing what its UI/label claims, in
production** — not when the code merges, not when CI is green, not when unit
tests pass.

- CI proves the code *runs*. It does not prove the code is *consumed*, or that
  the live environment matches the test environment.
- "Done" = observed working against prod (or the closest reachable real
  environment), with the verification noted.

> **Worked example — #104.** A CI-green PR took prod down with
> `UndefinedColumnError: column job_posting.jd_embedding does not exist`. CI ran
> against a DB where the migration applied; prod's didn't. "CI green" hid a
> total read outage.

## 2. Every feature needs a test that proves its CONSUMER consumes it

Not "the endpoint returns 200." Not "the column gets written." A test must prove
the **downstream consumer acts on the value**. If nothing consumes it, it is
not a feature — it is dead storage.

| Feature | A passing test must show… | NOT sufficient |
|---|---|---|
| `looking_for_text` | setting it **changes a posting's score / ranking** | row saved |
| "interested" action | marking a posting **makes it appear in a view** | action row written |
| per-company cap | changing it **changes which rows surface** (3→3, 6→6, 0→all) | param accepted |

> **Worked example.** `looking_for_text` (labelled "the strongest signal") and
> the pass reasons were stored and read by nothing — yet had passing tests that
> only proved the value was *stored*, never *used*.

## 3. No control or label may claim an effect the code doesn't produce

Every label, sub-label, help text, and status indicator must be **literally
true** about what the code does. A field that feeds nothing must not be labelled
as a signal; a status indicator must reflect real state, not a constant.

> **Worked examples.** `looking_for_text`'s sub-label read *"the strongest
> signal"* for a field consumed by nothing; the API-keys section **hardcoded
> "set"** for all five keys regardless of real env state.

## 4. Deploy gate: code must never go live ahead of its migration

A release must **fail** if the live DB schema lacks columns/tables the deployed
ORM selects. Code and schema move together, or the deploy is blocked.

**Implemented (feat/migration-deploy-gate):**
- **Layer 1 — atomic migrate-then-serve.** `apps/api/scripts/start.sh` runs
  `alembic upgrade head` then `exec uvicorn` under `set -euo pipefail`, using the
  **runtime** `DATABASE_URL`. A failed migration exits non-zero → the container
  never serves → the platform keeps the prior healthy deploy. Wired via
  `apps/api/Procfile` (Railway/Nixpacks) and the Dockerfile `ENTRYPOINT` on other
  hosts — the same script, host-agnostic.
- **Layer 2 — startup schema guard.** `job_assist.db.schema_guard` compares the
  DB's Alembic revision to the code head in the FastAPI lifespan (on in
  production via `SCHEMA_GUARD`); if behind, it **raises and refuses to serve**.
  Catches an entrypoint bypass.
- Migrations are **no longer** applied in a CI side-channel (`deploy.yml`) — that
  channel raced Railway's webhook auto-deploy and failed on a stale GitHub
  `DATABASE_URL` secret while Railway shipped the code anyway.

> **Worked example — #104 / #107.** Code that selected new columns deployed
> before its migration applied; every read 500'd. Layer 1 makes that
> impossible; Layer 2 refuses to serve if they ever diverge.
>
> **Operator note:** a Railway dashboard "Custom Start Command" overrides the
> Procfile — it must be empty (or set to `bash scripts/start.sh`) for Layer 1 to
> take effect.

## 5. The feature-reality audit is the standing backlog

The audit (every feature classified
**WORKING / INERT / BROKEN / MISLABELED / PARTIAL**) is the live debt backlog.

- Each non-WORKING item is a tracked fix.
- **Every fix lands with the guard test that would have caught the bug** (rule 2),
  so the same class of failure can't silently return.
- After a fix lands, re-verify in prod (rule 1) before moving it to WORKING.

> Open items (illustrative): "interested" hides into no view (BROKEN);
> `looking_for_text` / `role_keywords` / pass reasons inert; API-keys status
> hardcoded; Applied/Rejected attribution is company-level (PARTIAL); seniority
> parser under-levels (PARTIAL).

---

## The checklist (paste into every feature PR)

- [ ] **Consumer test** proves the downstream effect, not just that code runs (rule 2).
- [ ] **Labels are literally true** — no claimed effect the code doesn't produce (rule 3).
- [ ] **Schema/migration**: migration applies as the deploy role; ships atomically with the code (rule 4).
- [ ] **Prod verification**: observed doing what the UI claims, against prod (rule 1).
- [ ] If this fixes an audit item: the **guard test that would have caught the original bug** is included (rule 5).
