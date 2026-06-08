# Architecture docs

An accurate, code-traced map of the Job Assist system — the overview plus per-subsystem
zoom-ins (ingest, scoring/embeddings, Gmail→applied, self-maintaining crons, the proxy)
and two end-to-end lifecycle traces. Built for understanding the system *and* as a
portfolio artifact.

## The three deliverables

| File | What it's for | How to use |
|---|---|---|
| **`job-assist-architecture.html`** | **Interactive / explore.** Self-contained single file (Mermaid + pan-zoom inlined — no internet needed). | Open in any browser. Tabs across the top: overview → subsystems → lifecycle traces → hidden-deps → legend. Each diagram: **scroll = zoom, drag = pan, double-click = reset**. |
| **`job-assist-architecture.md`** | **Source of truth / portable.** The canonical document — prose + ` ```mermaid ` blocks. | Read on GitHub (renders the diagrams) or paste into any Markdown viewer. **Edit this file to change the architecture map** — the HTML and PDF are generated from it. |
| **`job-assist-architecture.pdf`** | **Shareable.** Clean linear export, all diagrams rendered. | Attach to a portfolio, email, or walk an interviewer through it. |

The HTML and PDF **both render from the embedded Markdown**, so all three are guaranteed
to show the same nodes, edges, and flows — they can't drift from each other.

## Visual encoding

🟦 internal app service · 🟦 (cyan) front end / proxy · 🟪 LLM (Gemini) hexagon ·
🟩 database cylinder · 🟧 external service/platform · solid edge = confirmed one-way ·
`<-->` = two-way · **dashed edge = inferred** · **red-dashed border = known failure point**.

## Accuracy posture

- **Read from the real codebase**, not memory — traced with `file:line` evidence across
  four subsystem passes (ingest+classifier, scoring+embeddings, Gmail+outcomes+applied,
  crons+health+proxy+frontend).
- Every edge is marked **confirmed** (seen in source) or **inferred** (reasonable but not
  directly wired — e.g. the Railway auto-deploy webhook, the ~7-day Gmail OAuth cadence).
  Ambiguities are flagged, not guessed.
- The LLM's exact position is made explicit in both lifecycle traces (Gemini is *decoupled
  from ingest* — a separate classifier sweep overwrites the regex `role_family` then
  rescores; every LLM call is one-way).
- **Last verified:** 2026-06-08, against `main`. Every Mermaid diagram was parsed and
  rendered through Mermaid itself during verification (all render, zero errors).

> If you change a wire in code, update `job-assist-architecture.md` and regenerate. The map
> is only as honest as its last trace.

## Regenerating the HTML + PDF

The two rendered files are built from `job-assist-architecture.md` by inlining it (plus the
vendored libs) into `_template.html`, then rendering with the Playwright Chromium that the
web app already installs for E2E.

**Build inputs (kept in-repo for reproducibility):**
- `_template.html` — the shell (tabs, pan-zoom, print mode) with `@@MARKDOWN@@`,
  `@@MERMAID_JS@@`, `@@SVGPANZOOM_JS@@`, `@@MARKED_JS@@` placeholders.
- `vendor/` — pinned `mermaid`, `svg-pan-zoom`, `marked` (inlined so the HTML is offline-safe).

**Steps**

1. Edit `job-assist-architecture.md` (the source of truth).
2. **Re-inline** → produce the single-file HTML: read `_template.html`, replace each
   placeholder with the file contents (escaping any literal `</script>` inside the inlined
   JS so it can't close the host `<script>`), write `job-assist-architecture.html`.
3. **Re-render the PDF** with Playwright: load the HTML at `…/job-assist-architecture.html#print`
   (print mode reveals all panels and stacks them), wait for `document.body[data-print-ready="1"]`,
   then `page.pdf({ format: 'A4', printBackground: true })` from `apps/web` (so `@playwright/test`
   resolves).

A minimal Node build script can do steps 2–3 in one pass; it was run from `apps/web` against
the installed Chromium (`chromium.executablePath()`), no extra downloads.

**Mermaid authoring gotchas** (both caught during verification — avoid them when editing the `.md`):
- A `;` inside a sequence-diagram message is a statement separator → **parse error**. Use `,` or `—`.
- `{...}` braces inside a `[...]` node label **render-error** (parse passes, render fails). Use `(...)`.
- Mermaid cannot measure geometry in a `display:none` container — the interactive HTML therefore
  renders each panel only when its tab is first shown.
