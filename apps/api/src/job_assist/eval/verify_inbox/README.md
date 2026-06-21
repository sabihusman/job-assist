# verify_inbox — LOCAL ONLY (never commit a filled sheet)

This is a **PUBLIC repo**. A filled verify sheet contains real JD text + email
subjects/snippets, so the whole directory is gitignored (`*` except this README
and `.gitignore`) — an edited sheet **cannot** be committed, even with
`git add -f` discipline lapses. Score the sheet **locally**; nothing with real
data leaves your machine via git.

## Local verify flow (no commit, no public exposure)

1. **Get the pre-labels** (the o3 JSONL from the generate run):
   ```
   gh run download <generate_run_id> -n eval-generate-<generate_run_id> -D _prelabels
   ```
   (Note: that artifact contains real JD text + email snippets — see the
   repo-root privacy note. Treat it as sensitive; delete it when done.)

2. **Build the verify sheet locally** from the pre-labels (there is no CI build
   — it would emit a public artifact with real data):
   ```
   uv run python -m job_assist.eval.run verify-build \
     --jsonl _prelabels/prelabels.*.jsonl --stamp local
   # → apps/api/src/job_assist/eval/datasets/verify_sheet.local.xlsx (gitignored)
   ```

3. **Edit** the `.xlsx` in Excel (confirm/correct the `verified_*` columns).
   Drop it here (e.g. `verify_inbox/edited.xlsx`) or anywhere local — it stays
   gitignored.

4. **Score locally** — computes override rates and writes the verified
   ground-truth labels (both outputs are gitignored):
   ```
   uv run python -m job_assist.eval.run verify-score \
     --jsonl _prelabels/prelabels.*.jsonl \
     --xlsx apps/api/src/job_assist/eval/verify_inbox/edited.xlsx \
     --stamp local
   # → verified_labels.local.jsonl + override_summary.local.json (gitignored)
   ```

There is **no CI `verify-score` workflow** — it was removed precisely so no path
expects a committed sheet. Keep all real-data scoring local.
