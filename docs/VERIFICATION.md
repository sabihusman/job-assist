# Verification Standard

The working standard for this repo. It applies to **every feature, old or new**.
It exists because this codebase has repeatedly shipped features that were
"built" and "CI green" but did nothing, claimed effects they didn't have, or
took production down. The rules below are the guardrails against exactly those
failures — each one cites the real incident it came from.

---

## 1. "Built" / "CI green" ≠ "working"

A feature is **DONE only when it is verified doing what its UI/label claims, in
production** — not when the code merges, not when CI is green, not when the unit
tests pass.

- CI proves the code *runs*. It does not prove the code is *consumed*, or that
  the live environment matches the test environment.
- Closing a task / merging a PR is not "done." Done = observed working against
  prod (or the closest reachable real environment), with the verification noted.

> **Worked example — #104 outage.** PR #104 was CI-green (DB-gated tests passed
> on CI Postgres) and merged. In production every `/postings` and
> `/operator/profile` request 500'd with
> `asyncpg.UndefinedColumnError: column job_posting.jd_embedding does not exist`.
> CI ran against a direct connection where the migration applied; prod's
> migration never added the columns. "CI green" hid a total read outage.

## 2. Every feature needs a test that proves its CONSUMER consumes it

Not "the endpoint returns 200." Not "the column gets written." A test must prove
the **downstream consumer actually acts on the value**. If nothing consumes it,
it is not a feature — it is dead storage.

Concrete bar (the test must demonstrate the *effect*, end to end):

| Feature | A passing test must show… | NOT sufficient |
|---|---|---|
| `looking_for_text` | setting it **changes a posting's score / ranking** | row saved to DB |
| "interested" action | marking a posting **makes it appear in a view** | `posting_action` row written |
| per-company cap | changing it **changes which rows surface** (cap=3→3, cap=6→6, cap=0→all) | param accepted (422 on negative) |
| pass reason | selecting it **changes future triage/scoring** | reason stored + displayed |

> **Worked examples — inert features found in the audit.**
> `looking_for_text` (labeled "the strongest signal") and `role_keywords` were
> stored and never read by the scorer or classifier. Pass reasons
> (`posting_action.reason`) were write-and-display-only — 65 recorded, 95%
> `wrong_role`/`too_senior`, consumed by nothing. All had passing tests that
> only proved the value was *stored*, never *used*.

## 3. No control or label may claim an effect the code doesn't produce

Every UI label, sub-label, help text, and status indicator must be **literally
true** about what the code does. If the effect isn't wired, the label must not
imply it is.

- A field that feeds nothing must not be labeled as a signal.
- A status indicator must reflect real state, not a hardcoded constant.

> **Worked examples — mislabeled in the audit.**
> (a) `looking_for_text`'s sub-label read *"free-form — the strongest signal"*
> for a field consumed by nothing. (b) The API-keys Settings section
> **hardcoded "set" for all five keys** regardless of actual env state — it
> would show a dead Gmail token as configured.

## 4. Deploy gate: code must never go live ahead of its migration

A release must **fail** if the live database schema lacks columns/tables the
deployed ORM selects. Code and schema move together or the deploy is blocked —
the app must not start serving a build whose migrations haven't applied.

Required guard (CI/deploy):

- A **schema-vs-ORM check** that, against the target database, asserts every
  column the ORM maps actually exists. Fails the release on skew.
- Migrations must apply (`alembic upgrade head` succeeds) **before** the new
  app instance receives traffic. A failed migration aborts the deploy; it never
  silently leaves new code running against an old schema.
- Migrations with environment-specific prerequisites (e.g. `CREATE EXTENSION`)
  must be verified runnable as the **deploy role**, or the prerequisite enabled
  out-of-band first, before the dependent code merges.

> **Worked example — #104, again.** The migration's `CREATE EXTENSION vector`
> failed on the Supabase deploy role's privilege, rolling back the whole
> migration (columns never created), while the new code deployed anyway and
> selected the missing columns. A schema-vs-ORM gate would have failed the
> release instead of taking prod down. (The privilege risk was even flagged in
> the migration's own docstring — and shipped regardless. Flags in a plan are
> not a substitute for a gate.)

## 5. The feature-reality audit is the standing backlog

The feature-reality audit (every feature classified
**WORKING / INERT / BROKEN / MISLABELED / PARTIAL**) is the live backlog of
debt. Rules:

- Each non-WORKING item is a tracked fix.
- **Every fix lands with the guard test that would have caught the bug** (per
  rule 2) — so the same class of failure cannot silently return.
- When a fix lands, re-verify in prod (rule 1) and move the item to WORKING only
  after that observation.

> Open items at the time of writing (illustrative, not exhaustive): "interested"
> hides postings into no view (BROKEN); per-company cap unreachable from the UI
> (effectively fixed-at-3); `looking_for_text` / `role_keywords` / pass reasons
> inert; API-keys status hardcoded; Applied/Rejected attribution is
> company-level (PARTIAL); seniority parser under-levels (PARTIAL).

---

## The checklist (paste into every feature PR)

- [ ] **Consumer test** proves the downstream effect, not just that code runs (rule 2).
- [ ] **Labels are literally true** — no claimed effect the code doesn't produce (rule 3).
- [ ] **Schema/migration**: migration applies as the deploy role; ORM-vs-schema gate green (rule 4).
- [ ] **Prod verification**: observed doing what the UI claims, against prod (rule 1).
- [ ] If this fixes an audit item: the **guard test that would have caught the original bug** is included (rule 5).
