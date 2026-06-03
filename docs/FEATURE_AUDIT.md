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

_Last updated: 2026-06-03._

---

## Recently resolved

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
| **`looking_for_text`** | MISLABELED + INERT | labelled "the strongest signal"; consumed by nothing (embedding Slice 1 was reverted) | misleads; no effect |
| **`role_keywords`** | INERT | stored; read by nothing (scorer uses hardcoded families) | lost intent |
| **Pass reasons** (`posting_action.reason`) | INERT | stored + displayed only; no scorer/classifier consumer | lost feedback signal |
| **API-keys Settings section** | MISLABELED | hardcodes "set" for all 5 keys regardless of real env state | can mask a missing key (e.g. dead Gmail token) |
| **`applicant_cap` hard rule** | INERT (in practice) | `applicant_count` is uniformly NULL (no adapter populates it) → cap never fires | a no-op the operator may trust |
| **Applied view** | PARTIAL | "applied" includes company-level `application_confirmation` | ↑ shows un-applied roles at a confirmed company |
| **Rejected view** | PARTIAL | rejection links at company level (`job_posting_id` NULL) | ↑ shows non-rejected roles as rejected |
| **Seniority parser** (ingest regex) | PARTIAL | under-levels Group/Head/Director; classifier sweep only fixes swept rows | ↑ senior roles leak into Triage |
| **Salary parser** | PARTIAL | no base-vs-OTE/commission detection; assumes base | ↑ commission roles look like base |
| **`staffing_firm_blocklist`** | UNVERIFIED | in profile; not confirmed a hard rule reads it | flag to confirm |

The root-cause fix for Applied/Rejected (both PARTIAL) is the deferred
per-posting `outcome_event` linker (`job_posting_id` is uniformly NULL).
