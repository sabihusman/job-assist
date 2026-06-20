# verify_inbox

Drop the **edited** verify sheet here (commit it) so the `verify-score`
workflow can read it. Default path: `verify_inbox/edited.xlsx` (override via the
workflow's `xlsx_path` input).

Flow:
1. Run the `A4 eval — verify-build` workflow with the generate run id → download
   the `verify-sheet-<id>` artifact.
2. Edit the `.xlsx` in Excel (confirm/correct the `verified_*` columns).
3. Commit it here (e.g. `verify_inbox/edited.xlsx`) on a branch.
4. Run `A4 eval — verify-score` with that branch + the same generate run id →
   it emits `verified_labels` + `override_summary` artifacts (Phase 3 input).
