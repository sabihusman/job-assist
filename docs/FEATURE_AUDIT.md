# Feature-Reality Audit (standing backlog)

The living debt backlog referenced by `docs/VERIFICATION.md` rule 5. Every
feature is classified by what it **actually does in production**, not what it
was built to do:

- **WORKING** — implemented, wired, and verified doing what its UI/label claims.
- **INERT** — implemented but consumed by nothing (dead storage).
- **BROKEN** — implemented but does the wrong thing.
- **MISLABELED** — works, but a label/status claims something false.
- **PARTIAL** — works for some inputs, silently degrades on others.

Rules (per VERIFICATION.md): each non-WORKING item is a tracked fix; every fix
lands with the guard test that would have caught the bug; an item moves to
WORKING only after **re-verification in production**.

_Last updated: 2026-06-18._

---

## Recently resolved

### `looking_for_text` / semantic signal — **WORKING** ✅ (was: MISLABELED)
Slice 2 landed: `similarity_score` is calibrated (0–100 PERCENT_RANK) and
`semantic_fit` is a live 20-weight feature inside `fit_score` for embedded rows
(~2,088/2,105 open). The semantic→score path is real, not aspirational. The
`best_fit_semantic` *sort* blend remains operator-tunable via `similarity_weight`
(0 = off). Verified via `/admin/diagnostics/semantic-readiness`.

### Score transparency (Version A1) — **WORKING** ✅ (new)
Every posting's `fit_score` is now fully explainable: `score_components` (JSONB)
records each sub-score, weight, contribution, renormalization, caps fired, and
`final`. `score_posting_decomposed()` is the single source of truth (`final ==
fit_score`, reconciled 2,239/2,239 in prod). Surfaced read-only via
`/admin/diagnostics/score-decomposition`.

### Applied-corpus boost (Version A3) — **WORKING (dormant)** ✅ (new)
A bounded, lift-only, eligibility-gated revealed-preference boost behind
`operator_profile.applied_corpus_weight` (**default 0 = no-op**, proven on prod:
0 drifted on a weight-0 rescore). Structurally cannot lift gated/disguised/senior
roles or bury anything. Read-only A2 signal exposed at
`/admin/diagnostics/applied-similarity`. Live but off until the operator raises
the weight.

### geo_whitelist US-remote — **WORKING** ✅ (was: PARTIAL)
The geo gate now passes US/unspecified-remote (`_remote_kind`) while still
failing region-qualified non-US remote (`Remote - India`, `EMEA Remote`). A
`reeval-hard-rules` backfill surfaced 4 genuine US-remote high-fit roles (+1
stale-eval correction); 0 wrongly surfaced.

### outcome-linking q4 (resume coverage) — **WORKING** ✅ (was: MISLABELED)
q4 anchored on `application_state` (only 2 legacy rows) → reported 2; now counts
`application_resume` directly → the true 12. `resume_text` backfill brought
paste-only 3 → 12 across `.docx` resumes.

### classifier non-object JSON — **WORKING** ✅ (was: latent BROKEN)
The relink classifier path is regression-tested against non-object Gemini JSON
(top-level array / bare string): degrades to unclassified, row left unlinked, no
raise (the `#198` classify() fix + relink's try/except, locked end-to-end).

### Reinstate a passed role — **WORKING** ✅ (new)
A "Reinstate" control on each Passed row returns the role to triage by appending
a `reset` action (append-only — the original `not_interested` + reason preserved).
Frontend-only; reuses the existing `reset` path.

---

## Earlier resolved

### Per-company cap — **WORKING** ✅ (was: silent suppressor)
The default Triage cap of 3 roles/company was enforced server-side but
**unreachable from the UI**, silently hiding viable roles (586 hard-rule
survivors → ~35 visible). Now operator-tunable.

- **Verified in production (PR #112):** migration `c9d0e1f2a3b4`
  (`operator_profile.per_company_cap`) **auto-applied via the deploy gate**
  (`start.sh` → `uv run alembic upgrade head`), the schema guard passed
  (`db == head`), and tunability is **live**:
  | cap | roles surfaced |
  |---|---|
  | 3 (default) | 35 |
  | 10 | 107 |
  | 0 (disabled) | 494 |
- **Operator-reachable:** Settings → "Roles per company" control (0 = Unlimited).
- **Guard tests:** profile-default, cap=4 intermediate, export-parity, FE
  save-sends-param (`test_per_company_cap.py`, `HardRulesSection.test.tsx`).
- **First audit item to move suppressor → WORKING under the verification
  standard** — fixed *and* re-verified in prod, with guard tests, behind the
  now-live migration-deploy gate.

### Migration-deploy gate — **WORKING** ✅
Root cause of the #104/#107 outages (code shipped ahead of its schema). Now:
Layer 1 (`scripts/start.sh` atomic migrate-then-serve) + Layer 2
(`schema_guard.py` startup revision check, path-resolution fixed in #111).
Verified end-to-end by #112 — the first migration-bearing deploy to apply
itself cleanly instead of taking prod down.

---

## Open backlog

| Feature | Status | Defect | Harm |
|---|---|---|---|
| **"Interested" view** | BROKEN | key `1` sets `interested`, posting leaves Triage into **no view** | ↓↓ suppresses operator's hand-picked best roles |
| **`role_keywords`** | INERT | stored; read by nothing (scorer uses hardcoded families) | lost intent |
| **Pass reasons** (`posting_action.reason`) | INERT | stored + displayed only; no scorer/classifier consumer. (Revealed-preference now feeds scoring via the A3 applied-corpus boost, but it reads the *applied* set, not pass *reasons*.) | lost feedback signal |
| **API-keys Settings section** | MISLABELED | hardcodes "set" for all 5 keys regardless of real env state | can mask a missing key (e.g. dead Gmail token) |
| **`applicant_cap` hard rule** | INERT (in practice) | `applicant_count` is uniformly NULL (no adapter populates it) → cap never fires | a no-op the operator may trust |
| **Applied view** | PARTIAL | "applied" includes company-level `application_confirmation` | ↑ shows un-applied roles at a confirmed company |
| **Rejected view** | PARTIAL | rejection links at company level (`job_posting_id` NULL) | ↑ shows non-rejected roles as rejected |
| **Seniority parser** (ingest regex) | PARTIAL | under-levels Group/Head/Director; classifier sweep only fixes swept rows | ↑ senior roles leak into Triage |
| **Salary parser** | PARTIAL | no base-vs-OTE/commission detection; assumes base | ↑ commission roles look like base |
| **`staffing_firm_blocklist`** | UNVERIFIED | in profile; not confirmed a hard rule reads it | flag to confirm |

The root-cause fix for Applied/Rejected (both PARTIAL) is the deferred
per-posting `outcome_event` linker (`job_posting_id` is uniformly NULL).
