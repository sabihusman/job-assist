# A4 eval — LOCAL-ONLY data flow (public repo)

This is a **PUBLIC** repo. The eval handles real application data — JD text,
email subjects/snippets, the operator's outcomes. **None of it may enter CI,
git, or a public artifact.** So the entire data-bearing pipeline runs on the
operator's machine; only **code, methodology, and aggregate counts** live in
CI/repo.

## What runs where

| Step | Where | Touches real data? |
|---|---|---|
| `count` (sample sizing) | **CI** (`eval-count` workflow) or local | No — emits **aggregate counts only** (distributions + pool sizes), never titles/text |
| `generate` (o3 pre-labels) | **LOCAL ONLY** | Yes — JD text + email snippets |
| `verify-build` (Excel sheet) | **LOCAL ONLY** | Yes |
| operator verifies the sheet | **LOCAL** (Excel) | Yes |
| `verify-score` (override rates + verified labels) | **LOCAL ONLY** | Yes |

There is **no CI workflow** for generate / build / score — they were removed so
no public path can emit real application data. All `datasets/` outputs and the
`verify_inbox/` sheet are gitignored.

## Local setup (once)

```
cd apps/api
uv sync                      # installs the dev group (openai, openpyxl)
# Secrets in your shell (never commit):
$env:OPENAI_API_KEY = "sk-..."          # the o-series pre-labeler
$env:API_URL        = "https://<prod-api>"
$env:API_AUTH_TOKEN = "<prod read token>"
$env:EVAL_OPENAI_MODEL = "o3"           # optional override (default o3)
$env:PYTHONPATH = "src"
```

## Full local pipeline

```
# 1. (optional) aggregate counts for sizing — safe to run in CI too
python -m job_assist.eval.run count --stamp local

# 2. generate o3 pre-labels over the stratified sample  → prelabels.local.jsonl
python -m job_assist.eval.run generate --stamp local

# 3. build the verify sheet                              → verify_sheet.local.xlsx
python -m job_assist.eval.run verify-build \
  --jsonl src/job_assist/eval/datasets/prelabels.local.jsonl --stamp local

# 4. edit verify_sheet.local.xlsx in Excel (confirm/correct verified_* columns)
#    drop it at src/job_assist/eval/verify_inbox/edited.xlsx (gitignored)

# 5. score                                  → verified_labels.local.jsonl + summary
python -m job_assist.eval.run verify-score \
  --jsonl src/job_assist/eval/datasets/prelabels.local.jsonl \
  --xlsx  src/job_assist/eval/verify_inbox/edited.xlsx --stamp local
```

Every artifact lands under `eval/datasets/` (or `verify_inbox/`), all gitignored
(`*.json`, `*.jsonl`, `*.xlsx`, `*.csv`). Nothing with real data can be committed
or pushed to a public artifact.

## Isolation invariant

`openai` is imported only in this package (enforced by
`tests/eval/test_isolation.py`); no production module imports `openai` or
`job_assist.eval`. The eval never runs in any cron/sweep/route/hot path.
