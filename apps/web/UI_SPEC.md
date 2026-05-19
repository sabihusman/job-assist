# Job Assist UI Spec (from Lovable build)

Source: https://job-nest-dash.lovable.app/ (build label "job-assist · build 0.4.0 · api railway-prod"). Theme system is built on Tailwind + CSS custom properties with light (default) and dark variants. Light theme is described in the Settings page itself as "Light · warm off-white default".

> **Capture status:** All seven pages (Triage, Applied, Pipeline, Companies, Outreach, Stats, Settings) and cross-page conventions captured. Triage and Applied sections preserve the original design-token-rich prose. Pipeline / Companies / Outreach / Stats / Settings were captured via a second Claude in Chrome pass focused on component structure, data fields, and behavior; their format is more explicitly structured. Responsive breakpoints (~1024px, ~640px) were not verifiable in the browser-extension environment — the harness viewport was locked at 1440px regardless of OS window size.

---

## Design tokens

Tokens are CSS custom properties defined on `:root` (light) and `.dark` (dark). All values are authored in `oklch()` — keep them in oklch in the Next.js port (Tailwind 4 supports it natively; on Tailwind 3 use the `oklch()` strings directly in the CSS file, not in the theme config). Approximate hex/RGB equivalents are given for reference only; the source of truth is the oklch value.

### Colors — Light theme (`:root`)

| Token | oklch | Approx hex | Use |
|---|---|---|---|
| `--background` | `oklch(98.5% .003 95)` | `#FBFAF7` | Page background (warm off-white) |
| `--foreground` | `oklch(20.5% 0 0)` | `#2A2A2A` | Primary text |
| `--surface` | `oklch(100% 0 0)` | `#FFFFFF` | Card / sidebar / detail-panel surface |
| `--surface-2` | `oklch(96.5% .003 95)` | `#F4F2EE` | Subtle inset surface (chip backgrounds, kanban count pills) |
| `--card` | `oklch(100% 0 0)` | `#FFFFFF` | Card bg (alias of surface) |
| `--card-foreground` | `oklch(20.5% 0 0)` | `#2A2A2A` | |
| `--muted` | `oklch(96.5% .003 95)` | `#F4F2EE` | Muted bg |
| `--muted-foreground` | `oklch(52% .01 255)` | `#7A7C84` | Secondary/tertiary text, meta, dividers labels |
| `--border` | `oklch(91% .004 95)` | `#E6E3DD` | Default border |
| `--border-strong` | `oklch(83% .004 95)` | `#D3CFC7` | Hover border on kanban cards |
| `--input` | `oklch(100% 0 0)` | `#FFFFFF` | Input field bg |
| `--popover` | `oklch(100% 0 0)` | `#FFFFFF` | Popover/dialog bg |
| `--primary` | `oklch(60% .11 215)` | teal-cyan ≈ `#2A8FA8` | Primary action / focus ring / brand |
| `--primary-foreground` | `oklch(99% 0 0)` | near-white | Text on primary |
| `--secondary` | `oklch(96.5% .003 95)` | `#F4F2EE` | Secondary button bg |
| `--accent` | `oklch(95% .005 95)` | `#F0EEEA` | Hover/active subtle bg |
| `--destructive` | `oklch(58% .21 27)` | red ≈ `#D7382C` | Destructive actions |
| `--positive` | `oklch(60% .11 215)` | same hue as primary | "interested", "remote" badge |
| `--positive-foreground` | `oklch(99% 0 0)` | | |
| `--negative` | `oklch(58% .21 27)` | red | Used for "pass / not interested" |
| `--negative-foreground` | `oklch(99% 0 0)` | | |
| `--pending` | `oklch(66% .16 55)` | amber ≈ `#C97A2C` | "snooze", "hybrid" badge, interview-stage pills |
| `--pending-foreground` | `oklch(99% 0 0)` | | |
| `--ring` | `oklch(60% .11 215)` | teal | Focus ring (matches primary) |
| `--tier-1` | `oklch(55% .15 150)` | green ≈ `#3E8B4A` | T1 badge |
| `--tier-2` | `oklch(52% .16 245)` | blue ≈ `#3460BC` | T2 badge |
| `--tier-3` | `oklch(50% .18 295)` | violet ≈ `#7240BC` | T3 badge |
| `--tier-4` | `oklch(55% .015 255)` | slate-gray ≈ `#7E828A` | T4 badge |
| `--ats-greenhouse` | `oklch(50% .14 150)` | darker green | GREENHOUSE badge text |
| `--ats-lever` | `oklch(60% .17 50)` | orange ≈ `#C97A2C` | LEVER badge text |
| `--ats-ashby` | `oklch(50% .16 285)` | indigo-violet | ASHBY badge text |
| `--radius` | `0.375rem` (6px) | — | Base radius |
| `--shadow-card` | `0 1px 2px 0 #00000008, 0 4px 12px -2px #0000000a` | — | Card shadow (very subtle in light) |

### Colors — Dark theme (`.dark`)

| Token | oklch | Notes |
|---|---|---|
| `--background` | `oklch(14.5% 0 0)` | Near-black neutral |
| `--foreground` | `oklch(93% 0 0)` | Near-white |
| `--surface` | `oklch(19.6% 0 0)` | Card surface |
| `--surface-2` | `oklch(23.5% 0 0)` | Inset surface |
| `--card` | `oklch(19.6% 0 0)` | |
| `--muted` | `oklch(23.5% 0 0)` | |
| `--muted-foreground` | `oklch(68% 0 0)` | |
| `--border` | `oklch(27% 0 0)` | |
| `--border-strong` | `oklch(34% 0 0)` | |
| `--primary` | `oklch(78% .13 195)` | Lighter teal |
| `--positive` | `oklch(78% .13 195)` | |
| `--negative` | `oklch(66% .16 35)` | Softer red |
| `--pending` | `oklch(78% .14 75)` | Softer amber |
| `--tier-1` | `oklch(74% .17 145)` | |
| `--tier-2` | `oklch(74% .14 230)` | |
| `--tier-3` | `oklch(74% .14 290)` | |
| `--tier-4` | `oklch(66% .04 250)` | |
| `--ats-greenhouse` | `oklch(70% .15 150)` | |
| `--ats-lever` | `oklch(72% .14 50)` | |
| `--ats-ashby` | `oklch(72% .14 280)` | |
| `--shadow-card` | `0 1px 2px 0 #0006, 0 4px 12px -2px #0000004d` | Heavier dark shadow |

### Typography

- UI font: `Inter, ui-sans-serif, system-ui, sans-serif` (in Next.js use `next/font/google` `Inter`).
- Monospace font: `"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace`. Used for: badges (tier, ATS, remote, score), header meta-strip ("J / K nav · 1-4 act · 2 → 1-7 reason"), salary ranges, ID values, kanban column headers, kanban count pills, calibration numbers, settings API-key names, footer/status line. **Load via `next/font/google`.**
- Base body size: `16px`. Default Tailwind line-height (1.5).
- Observed sizes (rendered px):
  - Page title H1 ("Triage", "Applied", "Pipeline", "Companies", "Outreach", "Stats", "Settings"): **14px / 600** — paired with 13px muted subtitle.
  - Triage detail panel H3 ("Linear"): **16px / 600**.
  - Section headings (h2): **14px / 600**.
  - Card company name: 14px / 600.
  - Card role title: **13px / 600**.
  - Card subtitle/description: **11px / 400** muted.
  - Card meta (location, salary): **12px / 400**.
  - Badges (tier, ATS, remote): **10px**, font-mono, uppercase, tracking-wide. Tier weight 500, ATS/remote weight 400.
  - Filter chips: **12px / 400**.
  - Filter group labels ("TIER", "SOURCE", "REMOTE", "FAMILY"): **~11px / 400** mono uppercase tracking-wide muted.
  - Kanban column header: **11px / 400** mono uppercase tracking-wider muted.
  - Kanban count pill: **10px / 400** mono on `bg-surface-2`.
  - Calibration numbers: mono, ~14–16px, bold.
  - Stats KPI numbers ("312", "1,184", "34%"): ~28–32px, weight 700.
  - Footer/sync line: 11–12px mono muted.
  - Detail-panel field labels ("Locations", "Salary", "Source", "First seen", "Score", "Family", "ID"): 12px / 400 muted.
  - Detail-panel field values: 13–14px / 400 foreground.
  - Sidebar "JOB ASSIST" wordmark: 12–13px / 700 tracking-wide uppercase.
  - Version line "v0.3 · local": ~11px mono muted.

### Spacing

- Base unit: **4px** (Tailwind default `0.25rem`). Common steps: `gap-1` (4), `gap-1.5` (6), `gap-2` (8), `gap-2.5` (10), `gap-3` (12), `gap-4` (16).
- Card list vertical gap: ~12px between triage cards.
- Card padding: `12px 16px` (Tailwind `py-3 px-4`).
- Kanban card padding: `10px` (`p-2.5`).
- Sidebar width: **224px** (`w-56`).
- Right detail panel width: **460px** (`w-[460px]`); visible at `lg` (≥1024px), hidden below.
- Header height: **48px** (`h-12`).
- Filter row sits below header, ~14–16px vertical padding.
- Kanban columns: `w-64` (256px), `gap-3` between columns, `p-4` outer.

### Borders, shadows, radii

- Base radius `--radius: 0.375rem` (6px). Actual usage:
  - Triage cards: 6px (`rounded-md`).
  - Kanban cards / chips / badges: 4px (`rounded`).
  - Pill counts and small badges: 4px.
  - Avatar: 6px (rounded square, not full circle).
  - Buttons: 4–6px depending on size.
- Borders: 1px solid `--border`. Hover often elevates to `--border-strong`.
- Badges use a `ring-1 ring-inset ring-{color}/30` + `bg-{color}/15` pattern. Example: T1 badge = `bg-tier-1/15 text-tier-1 ring-tier-1/30`. Consistent across tier / ATS / remote / outcome.
- Shadows:
  - Card light: `0 1px 2px 0 rgba(0,0,0,0.03), 0 4px 12px -2px rgba(0,0,0,0.04)`.
  - Card dark: `0 1px 2px 0 rgba(0,0,0,0.4), 0 4px 12px -2px rgba(0,0,0,0.3)`.
  - Command palette dialog: `shadow-2xl` (Tailwind default).
- Focus ring: 2px `--ring` (teal), Tailwind `focus-visible:ring-2 focus-visible:ring-ring`.

---

## Global layout

Three-region application chrome:

```
┌──────────────┬──────────── top header (sticky, 48px) ────────────┐
│              ├────────────────────────────────────────────────────┤
│   sidebar    │                                  │   right         │
│   (224px)    │      main content                │   detail panel  │
│              │                                  │   (460px, lg+)  │
│              │                                  │                 │
│              │                                  │   actions bar   │
│              │                                  │   (sticky bot)  │
└──────────────┴──────────────────────────────────┴─────────────────┘
   sync footer
```

The right detail panel is Triage-specific. Other pages use the full main content width.

### Header (banner)

- Sticky top, 48px tall, white surface, 1px bottom border (`border-border`), background with slight translucency (`oklab(0.985 ... / 0.9)`).
- **Left cluster:** sidebar-toggle icon button (small ghost) + page title H1 (`14px / 600`) + muted subtitle. Subtitles per page:
  - Triage: "22 pending · 5 applied"
  - Applied: "5 active applications"
  - Pipeline: "Kanban by outcome stage"
  - Companies: "16 target companies · 1 closed"
  - Outreach: "3 pending drafts · Week 3 preview"
  - Stats: "Operator metrics"
  - Settings: "Operator tuning interface · single-user"
- **Center:** "Jump to…" search button. Width ~280–320px, `bg-surface`, 1px `border-border`, `rounded`, magnifying-glass icon left, `⌘K` kbd chip right (mono 10px on `bg-surface-2` pill). Opens command palette.
- **Right meta strip** (Triage only): mono helpline `J / K nav · 1-4 act · 2 → 1-7 reason` with each key as `<kbd>` chip (small rounded `bg-surface-2`, 1px border, 10–11px mono, padding 2px 5px).
- **Per-page additions to the right cluster:**
  - Outreach: small **"BETA"** pill (orange/amber).
  - Companies: ghost-bordered `+ Add company` button.

### Sidebar (left nav)

See full cross-page convention block at the end of this doc for sidebar nav structure, badges, "SAVED FILTERS" section, and collapsed-state behavior.

### Footer / status bar

No global footer outside sidebar's sync line, except on Settings — centered footer: `job-assist · build 0.4.0 · api railway-prod · last sync 14s ago` (11px mono muted).

---

## Pages

### Home / Triage queue (`/`)

Title: "Triage". Subtitle: "22 pending · 5 applied". Layout: **main column + right detail panel** (split). Detail panel hidden below `lg` breakpoint.

**Filter row (under header):**
- Horizontal row, four groups separated by visual whitespace. Each prefixed by 11px mono uppercase muted label:
  - `TIER` → chips `T1 T2 T3 T4`
  - `SOURCE` → chips `greenhouse lever ashby`
  - `REMOTE` → chips `remote hybrid onsite`
  - Row 2: `FAMILY` → chips `Product Mgmt Product Owner Product Marketing Program Mgmt`
- Each chip: `px-2 py-0.5 text-xs rounded ring-1 ring-inset ring-border bg-surface text-muted-foreground hover:text-foreground`. Selected: `bg-accent text-foreground ring-border-strong` (multi-select).
- Right side: small label `showing 22 of 42` in 12px muted.

**Calibration card (under filters):**
- Full-width card on `bg-card`, 1px border, 6px radius, ~16px padding.
- **Top:** sparkle/zap icon + uppercase tracking-wider label `THIS WEEK'S CALIBRATION` (11–12px mono).
- **Body:** four inline KPI pairs separated by dot/space:
  - `SURFACED 42`
  - `INTERESTED 13 (31%)` — percentage in mono parens, "13" bold/larger
  - `APPLIED 8`
  - `REJECTED BY YOU 11`
  - Labels small uppercase muted, values bold foreground (interested green, rejected red, etc. — colors from semantic tokens).
- **Below:** muted text `Top "wrong" reasons: program manager (4), product marketing (2)` with reason words rendered as `bg-surface-2` inline chips.
- **Top-right:** outline button `Tune surfacing →` → links to `/settings`.

**Triage card (primary repeating component):**
- White card, 6px radius, 1px border, subtle shadow, `p-3 px-4`. Hover → border darkens to `border-border-strong`, bg shifts to faint warmer surface.
- **Tier strip:** absolutely positioned vertical bar at left edge — `absolute left-0 top-3 h-[calc(100%-1.5rem)] w-0.5 rounded-r` colored by tier (T1 = `bg-tier-1`, etc.). Selected card in live build uses `bg-primary` for the strip; use tier color in port.
- **Layout (flex row):** `[Avatar 32×32] [main column flex-1] [actions column]`.
- **Avatar:** 32×32 `rounded-md`, white letter centered, font-semibold ~13.4px. Background color deterministic per company (hashed hue). Examples: Linear `L` → orange/red `oklch(0.62 0.13 35)`; Vercel `V` → green; Stripe `S` → orange/red; Notion `N` → orange/red; PostHog `P` → similar warm.
- **Main column line 1:** `[Company name] [Tier badge] · [ATS badge] · [🕒 timestamp]` — company 14px/600; tier badge styled as below; "·" 12px muted middot; timestamp like "3h ago", "6h ago", "1d ago", "5d ago" in 11–12px mono muted with `lucide-clock` prefix.
- **Main column line 2:** muted 11px tagline ("Issue tracking built for high-velocity software teams").
- **Main column line 3:** role title (e.g. "Senior Product Manager, Platform") in 13px / 600 foreground.
- **Main column line 4 (meta row):** `📍 [location] · [salary mono] [remote-type badge] score —` — location with `lucide-map-pin` icon; salary mono (e.g. `$240k–$300k`); remote badge; literal `score —` in muted (em-dash = placeholder for unscored).
- **Actions column (right):** four small outline icon buttons in a row, each `h-7`, `rounded`, `border-border`, `bg-surface`, `text-muted-foreground`, `px-2`. Icon + small mono digit `1`/`2`/`3`/`4` after it. Tooltips: `Interested · 1`, `Not interested · 2`, `Applied · 3`, `Snooze · 4`. Hover colorization:
  - Button 1 (interested): `hover:bg-positive/15 hover:text-positive`
  - Button 2 (not interested): `hover:bg-negative/15 hover:text-negative`
  - Button 3 (applied): `hover:bg-primary/15 hover:text-primary`
  - Button 4 (snooze): `hover:bg-pending/15 hover:text-pending`
  - Icons: lucide `check`, `x`, `send`/`paper-plane`, `clock`/`alarm-clock`.
- **Card states:**
  - **Default:** as above.
  - **Selected** (mirrored in right panel): tier strip becomes solid teal (`bg-primary`).
  - **Hover:** border-strong + slight `bg-accent/30` warmth.
  - **Expanded with reason picker** (after clicking action 2/"Pass"): below meta row, labelled chip-grid appears titled `WHY NOT?` (uppercase 11px mono muted). 7 reason chips in 2–3 rows, each `outline button rounded border-border bg-surface px-2 py-1 text-xs`. Suffix mono hotkey:
    - `Wrong role 1` · `Wrong location 2` · `Comp too low 3` · `Wrong industry 4` · `Wrong stage 5` · `Already rejected here 6` · `Just not feeling it 7`
    - Right-aligned `× esc` chip (24×24 ghost) to cancel.
    - Inline within card (no popover). Clicking a reason commits the pass with that reason.

**Right detail panel** (visible ≥lg, 460px, `bg-surface`, `border-l border-border`, sticky/scrolling on its own):
- **Top mini header** (sticky inside panel): small T1 tier badge + company name (14px/600 truncated) left; right cluster: `Open JD` external-link icon button + close `×` icon button.
- **Hero block:** 56×56 rounded-md company avatar (same palette as cards), company name H3 16px/600, tier badge, small uppercase mono muted line `DEV TOOLS / PROJECT MANAGEMENT` (industry).
- **Body intro paragraph:** 14px foreground, company description ("Linear builds an opinionated issue tracking and project management tool…").
- **Section:** role title H2 14px/600.
- **4-column key/value grid** (label = 12px muted, value = 13px foreground or badge):
  - `Locations | San Francisco, CA` `Remote | HYBRID badge`
  - `Salary | $240k–$300k mono` `Source | ASHBY badge`
  - `First seen | 3h ago` `Score | —`
  - `Family | Product Management` `ID | p-0 mono`
- **Section:** `BUSINESS DIVISION FOR THIS ROLE` (uppercase 11px mono muted) → callout box (rounded, 1px border, subtle `bg-surface-2`, ~12px padding) with small amber/pending dot + italic text `Division info pending — will populate after next enrichment run`.
- **Section:** `JOB DESCRIPTION` (same label style) → raw markdown rendered as prose: H2 sub-heads (`## About the role`, `## What you'll do`, `## What we look for`, `## Compensation`), unordered lists with `-` bullets, body paragraphs. Markdown rendered in 13–14px foreground, line-height ~1.55. Headings inside prose: 14–15px / 600.
- **Sticky bottom action bar** (full-width, top border): four equal-width outline buttons:
  - `[✓] Interested 1` (hover positive)
  - `[×] Pass 2` (hover negative; clicking expands reason picker)
  - `[➤] Applied 3` (hover primary)
  - `[🕒] Snooze 4` (hover pending)
  - Each button shows hotkey as small mono `1`/`2`/`3`/`4` chip right of label.

**Loading state:** No global skeleton observed. Calibration card's pattern + muted "—" placeholders suggest content-led empty pattern, not skeletons.

**Empty state:** With filters matching nothing, "showing 22 of 42" → "showing 0 of 42" + card list disappears. Not directly observed; **TODO when porting** — add empty state "No postings match your filters." + "Reset filters" link.

**Keyboard cues (visible on Triage):**
- `J / K` — navigate between cards
- `1`–`4` — act (interested/pass/applied/snooze)
- `2 →` then `1`–`7` — pass + reason
- `⌘K` (or `Ctrl+K`) — command palette
- `G T / G A / G P / G C / G O / G S` — go to Triage/Applied/Pipeline/Companies/Outreach/Stats (two-key sequence, visually advertised but NOT currently wired in the live build — see Cross-page conventions for status)
- `esc` — close reason picker / dialog

---

### Applied (`/applied`)

Title: "Applied". Subtitle: "5 active applications". Layout: **full-width list**, no right panel.

- Top-right of header: sort/filter strip `sort: applied | stage | tier` — three pill toggle buttons. Active: `bg-surface-2` + foreground text. Others: muted.
- Body: vertical list of "applied" cards, each single row that **expands inline** to reveal a Timeline.
- **Collapsed row** (left → right):
  - Disclosure chevron `›` (rotates to `⌄` when open)
  - Tier badge (T1/T3 etc.)
  - Company name (14px/600) `·` Role title (foreground)
  - Below: muted meta line `applied May 16` + `1d ago` + `$210k–$270k` (mono) + ATS badge
  - Far right: outcome-stage badge (per-stage hue — `Applied` = tier-1 green, `Recruiter screen` / `Phone interview` / `Video interview` = pending/amber, etc.)
- **Expanded row** reveals inset `TIMELINE` block:
  - Label `TIMELINE` (uppercase 11px mono muted)
  - Vertical timeline: thin left guide line in `border` color, circular dots (filled `bg-primary` / `bg-positive`) at each event row
  - Each event: `[dot] [stage badge] ………… [date · relative-time]`. Examples:
    - `• [Applied] · May 7 · 10d ago`
    - `• [Recruiter screen] · May 10 · 7d ago`
    - `• [Phone interview] · May 13 · 4d ago`
  - Stage badges use same outcome-stage color system as right-side badges.
- **Empty state:** Would say "No active applications." Not observable.

---

### Pipeline (`/pipeline`)

Title: "Pipeline". Subtitle: "Kanban by outcome stage". Layout: horizontally-scrolling kanban; no right panel.

**Layout regions:**
- Top banner — standard (sidebar toggle, title + subtitle, "Jump to…").
- Main content — horizontally-scrolling kanban with **8 fixed stage columns**, left to right: `APPLIED`, `RECRUITER`, `PHONE`, `VIDEO`, `ONSITE`, `OFFER`, `REJECTED`, `GHOSTED`. The earlier original-spec note that mentioned 9 columns was an over-count; the live DOM shows 8.

**Components:**
- **Stage column header:** uppercase mono label + count pill (e.g. `APPLIED 2`, `RECRUITER 1`, `OFFER 0`). Not interactive — no sort/filter controls per column.
- **Application card** (one per item in a column):
  - Type: card (not a button; no observed click handler — clicking does not open a detail panel or navigate in the live build). No drag handles observed.
  - Tier badge (`T1`, `T3`, etc., accessible labels `Tier 1` / `Tier 3`)
  - Company name (e.g. "Figma", "Twilio")
  - Role line (e.g. "Product Owner, Payments")
  - Date (e.g. "May 11")

**Data fields:**
- `stage_name`: string, uppercase (structural, fixed set of 8)
- `stage_count`: integer (frequently changing)
- `card.tier`: enum "T1"–"T4"
- `card.company`: string
- `card.role`: string, format "{role}, {family}"
- `card.date`: string, "MMM D"

**States:**
- Loading: not observed (pages hydrate synchronously).
- Empty column: rendered as "—" (em dash) centered in the column body.
- Whole-page empty: not observed.
- Error: not observed.

**Modal flows from this page:** command palette only.

**Responsive notes:** not verifiable (1440px-locked harness). Kanban already requires horizontal scroll at 1440px; presumably continues to scroll on narrower viewports.

**Frequently changing copy:** stage counts, card tier badges, card company/role/date values.

**Structural copy:** "Pipeline", "Kanban by outcome stage", stage names (8 verbatim listed above).

**Port-time TODO:** card click behavior is unwired in the live build. The Next.js port should decide: open the same right detail panel as Triage, navigate to `/postings/{id}`, or keep static. Recommend opening detail panel modally for consistency with Triage.

---

### Companies (`/companies`)

Title: "Companies". Subtitle: "16 target companies · 1 closed" (count + closed-count interpolated in structural template). Layout: full-width single-table.

**Banner additions:** `+ Add company` button on far right. Behavior currently unwired in the live build — clicking produced no observable state change. Recommend hiding for v1 of the port (the target list is operator-curated, not user-additive yet) OR wiring a small POST endpoint for target_company creation.

**Components — Companies table:**
- Column headers (uppercase mono, not clickable for sort): `NAME`, `TIER`, `ATS`, `OPEN`, `APPLIED`, `OUTCOMES`, `NOTES`. Plus an unnamed trailing row-action column for `close` / `reopen`.
- **Row name cell:** company name (e.g. "Linear", "Snyk"); closed companies show an inline `closed` tag next to the name.
- **Tier badge:** "T1"–"T4" with accessible label "Tier N".
- **ATS cell:** composite — ATS provider badge (`ASHBY` / `GREENHOUSE` / `LEVER`) + handle path (e.g. `/linear`, `/vercel`).
- **Open count / Applied count:** integers.
- **Outcomes cell:** free-text summary ("2 screens, 1 onsite", "1 rejection", "No response yet"). Empty rendered as "—".
- **Notes cell:** click-to-edit inline. Empty state shows placeholder "add note…" (italic muted). Clicking turns the cell into a single-line text input prefilled with current value. Escape collapses without saving (verified). Truncates with `…` when not editing.
- **Row action ("close" / "reopen"):** rendered as muted text on the right edge of each row. First-click reaction observed: label turned red (apparent confirm-state). Behavior beyond first click was unclear in the live build — may require confirmation flow not surfaced.

**Data fields:**
- `company.name`: string (~max 12 chars in observed data)
- `company.tier`: enum "T1"–"T4"
- `company.ats`: enum "ashby" | "greenhouse" | "lever" (rendered uppercase)
- `company.handle`: string, `/{slug}` form
- `company.open`: integer
- `company.applied`: integer
- `company.outcomes`: string, placeholder "—" when empty
- `company.note`: string, placeholder "add note…" when empty
- `company.status`: "open" | "closed" — closed inserts inline `closed` tag, flips row action label to `reopen`

**States:**
- Loading / global empty / error: not observed.
- Notes cell empty: "add note…"
- Outcomes cell empty: "—"

**Modal flows from this page:** command palette only. Notes editing is inline, not modal.

**Port-time prerequisites:**
- The inline notes editor requires a backend dependency NOT yet built: a `target_company.notes` column + a PATCH endpoint. **Decision needed for PR #32:** strip the notes column from v1, OR add a small pre-req migration + endpoint PR.
- The "close" row action requires the existing `closed_channel` table backing — confirm wiring before relying on it.

**Frequently changing copy:** subtitle counts, all per-row data (name, tier, ATS, handle, open, applied, outcomes, notes).

**Structural copy:** "Companies", "+ Add company", column headers, placeholders "add note…" / "—", row actions "close" / "reopen", `closed` tag.

---

### Outreach (`/outreach`)

Title: "Outreach". Subtitle: "{N} pending drafts · Week 3 preview" (count is interpolated; observed transitioning 3 → 2 → 1 → 0 as drafts were actioned). Layout: vertically stacked draft cards, centered, no right panel.

**Banner additions:** small static "BETA" tag rendered after "Jump to…". Sidebar nav item shows badge `3` (pending-drafts count).

**Components — Draft card** (one per draft):
- Recipient name (e.g. "Anna Hoffmann")
- Recipient title (e.g. "Senior Recruiter, Platform") — separated from name by "·"
- Route line: format `{company} → {role} · drafted {relative-time}` (e.g. "Linear → Senior Product Manager, Platform · drafted 4h ago")
- Character count (e.g. "214/300") in top-right of card. 300-char cap is fixed.
- Message body — text in a rounded read-only container; switches to an editable textarea when "Edit" is clicked.
- **Actions row:**
  - `Mark sent` — removes card from queue, decrements pending-drafts count by 1
  - `Approve` — toggleable. Idle ↔ selected/active. Doesn't remove the card on its own; visual toggle only in current build.
  - `Edit` / `Done` — toggles body between read-only and editable textarea (still character-counted)
  - `Skip` — removes card from queue, decrements count by 1 (rendered slightly separated on the far right of the action row)

**Data fields:**
- `draft.recipient_name`: string
- `draft.recipient_title`: string
- `draft.company`: string
- `draft.target_role`: string
- `draft.drafted_at`: rendered as relative time ("4h ago", "1d ago"). Note: initial render in the Lovable preview showed `drafted 686mo ago` for placeholder/seed timestamps — flag as a stale-seed-data artifact, not a real bug. Real `posting_action.created_at` rows will never produce this.
- `draft.length`: "{n}/300" integer ratio
- `draft.body`: long text string

**States:**
- Loading: not observed.
- Empty (no drafts queued, after all actioned):
  - Icon: chat bubble glyph (centered)
  - Heading: "No drafts queued"
  - Body: "The next outreach cycle runs nightly. Drafts will appear here for approval before you send manually from LinkedIn."
- Error: not observed.

**Modal flows from this page:** command palette only. Edit is inline, not modal.

**Frequently changing copy:** subtitle count, sidebar Outreach badge count, all per-card data, character count, Approve toggle state.

**Structural copy:** "Outreach", "Week 3 preview", "BETA", button labels ("Mark sent", "Approve", "Edit", "Done", "Skip"), empty-state heading and body verbatim above.

---

### Stats (`/stats`)

Title: "Stats". Subtitle: "Operator metrics". Layout: full-width, three stacked sections (KPI grid, outcome funnel, source effectiveness). No interactive controls beyond the standard banner.

**Layout regions:**
- KPI card grid (top) — at 1440px renders as 4-up row then 3-up row, **7 cards total**
- "OUTCOME FUNNEL" section card (middle) — 6 horizontal stage bars
- "SOURCE EFFECTIVENESS" section card (bottom) — 4-column table

**Components — KPI cards (7 read-only cards):**
- `POSTINGS INGESTED (last 7d)` — value 312, secondary delta "+18%"
- `POSTINGS INGESTED (last 30d)` — value 1,184
- `APPLICATIONS (last 7d)` — value 14, delta "+4"
- `APPLICATIONS (last 30d)` — value 64
- `RESPONSE RATE` — value "34%", caption "screens / applied"
- `AVG TIME TO FIRST RESPONSE` — value "4.2d", caption "across active apps"
- `OFFER RATE` — value "1.6%", caption "offers / applied"

Each card: uppercase small-caps label header + large metric value (28–32px / 700) + optional inline signed delta + optional footnote caption.

**Components — Outcome funnel:**
- Section label: "OUTCOME FUNNEL" (uppercase)
- 6 stage rows (verbatim labels in order): "Applied", "Recruiter screen", "Phone interview", "Video interview", "Onsite", "Offer"
- Each row: stage label + horizontal bar (count embedded in bar) + percent-of-applied to the right + drop-off-from-prior `↓ NN%` (final stage has no drop-off)
- Observed values: Applied 64/100% (↓66%), Recruiter screen 22/34% (↓35%), Phone interview 14/22% (↓36%), Video interview 9/14% (↓57%), Onsite 4/6% (↓73%), Offer 1/2%.

**Components — Source effectiveness:**
- Section label: "SOURCE EFFECTIVENESS"
- Column headers: `SOURCE`, `APPLIED`, `SCREENED`, `RATE`, `DISTRIBUTION`
- Rows: per-source breakdown. Observed: "Warm intro" 3/3/100%, "Greenhouse" 31/12/39%, "Lever" 18/6/33%, "Ashby" 12/3/25%.
- `DISTRIBUTION` column is a horizontal bar visualization proportional to rate.

**Data fields:**
- KPI: `label` (string), `value` (number, formatted with comma / % / d as appropriate), `delta` (signed number, observed positive only), `caption` (string)
- Funnel row: `stage` (string), `count` (int), `percent_of_top` (int %), `drop_off` (int % or absent on final)
- Source row: `source` (string), `applied` (int), `screened` (int), `rate` (int %), `distribution` (bar proportional to rate)

**States:** loading / empty / error — none observed (page does not surface a global empty state with current data).

**Modal flows from this page:** command palette only.

**Port-time prerequisite:** the "SOURCE EFFECTIVENESS" panel requires a backend endpoint NOT shipped by PR #30b. The current `/stats/funnel` and `/stats/calibration` cover the funnel + calibration card; per-source effectiveness needs either an extension to `/stats/funnel` or a new endpoint. **Decision needed for PR #32:** strip this panel from v1, OR add a small pre-req endpoint PR.

**Frequently changing copy:** all KPI values and deltas, funnel counts/percents/drop-offs, source row counts/rates.

**Structural copy:** "Stats", "Operator metrics", section labels, KPI card labels (7 verbatim), KPI captions, funnel stage labels (6 verbatim), source table headers.

---

### Settings (`/settings`)

Title: "Settings". Subtitle: "Operator tuning interface · single-user". Layout: vertically stacked sections, each a two-column layout (label column on left, control column on right) with a section heading.

**Sections in order:** Appearance · Profile · Hard rule thresholds · API keys · Manual job triggers.

**Page footer:** `contentinfo` element at the bottom — `job-assist · build 0.4.0 · api railway-prod · last sync 14s ago`. **Only present on this page.**

#### Section: Appearance

- Description: "Light by default. Dark mode is here when you want it."
- **Theme row** — Label "Theme" + sub "Light · warm off-white default" (echoes current value; flips to "Dark · low-light operator mode" when Dark active). Segmented button group: `Light` (default active) / `Dark`. Clicking toggles theme.
- **UI scale row** — Label "UI scale" + sub "Scales all text, spacing, and cards. 2% steps, up to +20%." Controls: `−` decrement / current value readout (`+0%`, `+4%`, etc.) / `+` increment / `reset` button / range slider (0% → +20% with tick marks at 0/+10/+20). Default 0%.

#### Section: Profile

- Description: "Identity, scope, and the free-form signal that drives scoring."
- **Name** — text input, default value "Alex Morgan".
- **Current role keywords** — sub "press Enter to add". Tag-input pattern: existing tags as chips with `×` remove (default values "product manager", "senior pm", "wealthtech pm"). Trailing input placeholder "add keyword…". Enter commits.
- **Geography whitelist** — same pattern. Default tags "Des Moines", "Remote US", "NYC", "Austin". Placeholder "add location…".
- **What I'm looking for right now** — sub "free-form — the strongest signal". Textarea prefilled with example content. Helper below: "This is the most important signal the scoring system uses. Rewrite anytime your preferences shift."
- **Save profile** button (section-scoped submit). No visible dirty indication in the live build (button stays in idle/outlined appearance regardless of edits). On save, triggers toast `✓ Profile saved · vector rewritten`.

#### Section: Hard rule thresholds

- Description: "Filters applied before triage. Slider previews are computed against last week's surfaced postings."
- **Maximum applicant count** — sub "Drop postings above this applicant count." Live readout, numeric input, range slider (min=50, max=500, step=10, default 150). Below: live preview `Would drop {N} of last week's postings.` updates as slider moves.
- **Salary floor (annual USD)** — sub "Drop postings whose max salary falls below this." Same triple-control pattern. Slider min=50000, max=300000, step=5000, default 85000 (displayed as "$85K"). Same live preview line.
- **Closed channels** — sub "Companies you've explicitly opted out of." Existing rows: company name + reason line + date + `×` remove. Date renders as `MMM D` for historical rows, literal word `today` for newly-added rows. Add-row inputs: placeholders `Company` and `Reason` + `Add` button. Default rows: MetaCorp (Compensation cap below floor, Mar 12), BigBlueCo (Onsite required, no remote option, Feb 28).
- **Staffing firm blocklist** — sub "one firm per line". Textarea, one firm per line, default "Robert Half / Aerotek / Insight Global / Apex Systems".
- **Role family weights** — sub "0.0 = never surface · 1.0 = full weight". Per-family slider + readout (0.00–1.00, step 0.05, two-decimal display). Default families: Product Management (1.00), Product Owner (0.60), Product Marketing (0.30), Program Manager (0.30). Helper below: "How aggressively to surface each role family."
- **Section-footer live preview line:** `live preview: drop {drop_n} · surface {surface_n} new` — updates as any slider in the section moves.
- **Save hard rules** button — dirty indication: button gains a filled/highlighted (light teal) background when the section has unsaved changes. Click triggers a confirmation modal (see below). On apply, triggers toast `✓ Rules applied · dropped {N} · surfaced {N}`.

#### Section: API keys

- Description: "Read-only · values are stored on the backend and not retrievable."
- List of env-var rows. Each: env var name (left) + optional milestone tag inline (e.g. `Week 4` on ANTHROPIC_API_KEY) + status pill on right (`set` with neutral dot OR `missing` with red dot — **color encodes meaning here**).
- Default rows observed: DATABASE_URL (set), GEMINI_API_KEY (set), ANTHROPIC_API_KEY (Week 4 tag, missing), GMAIL_CREDENTIALS_JSON (set), GMAIL_REFRESH_TOKEN (set).
- No interactive controls.

#### Section: Manual job triggers

- Description: "POSTs to backend admin endpoints. Output stays in place."
- Per-row: title + `POST {endpoint}` subtitle + optional text input (when endpoint has a path param) + `▶ run` button.
- Default rows:
  - "Run discover-ats" — `POST /admin/discover-ats/run?commit=false`
  - "Run Gmail backfill (60 days)" — `POST /admin/gmail/backfill?days=60`
  - "Run Greenhouse ingestion" — `POST /admin/ingest/greenhouse/{handle}` — with an additional text input (placeholder "handle") before the run button.
- **Run button behavior:** Idle "▶ run" → in-flight "running…" (disabled) → success: inline panel opens below the row labeled "RESPONSE" (uppercase) containing the raw JSON pretty-printed, with an `×` dismiss control top-right. Output stays in place; no navigation, no toast. Observed discover-ats payload fields: `ok` (bool), `commit` (bool), `discovered` (int), `new_handles` (string[]), `duration_ms` (int).

#### Modal flow: Apply rule changes confirmation

Triggered by clicking `Save hard rules` while the Hard rule thresholds section is dirty.

- Heading: "Apply rule changes?"
- Body: "These changes will drop {N} postings from the current queue and surface {N} new ones." (interpolated counts, red-highlighted, match the live preview line)
- Buttons:
  - `Cancel` — secondary; dismisses modal without applying.
  - `Apply & refresh queue` — primary filled; applies changes, closes modal, fires toast `✓ Rules applied · dropped {N} · surfaced {N}`.

#### Settings page — states summary

- Loading: not observed.
- Empty: no global empty state (always populated). Section-local placeholders observed only in input fields ("add keyword…", "add location…", "Company", "Reason", "handle").
- Error: not observed.
- Dirty: only the Hard rule thresholds section visually indicates dirty (Save button gains filled background). Save profile button stays in idle appearance regardless of edits.

---

## Cross-page conventions

### Sidebar (full structure)

- **Brand block:** 32×32 rounded-square teal "J" tile (`bg-primary` with white "J"), two-line wordmark:
  - Line 1: `JOB ASSIST` (uppercase, bold, 12–13px tracking-wide)
  - Line 2: `v0.3 · local` (mono, muted, ~11px)
- **Primary nav items** (in order, each = lucide icon + label + optional right-aligned count badge in mono on `bg-surface-2`):
  - Triage (`mail`/inbox icon) — badge `24` (pending-triage count, frequently changing)
  - Applied (`activity`) — no badge
  - Pipeline (`columns`/`kanban-square`) — no badge
  - Companies (`building`) — no badge
  - Outreach (`message-square`) — badge `3` (pending-drafts count, observed decrementing to `0` when all drafts actioned)
  - Stats (`bar-chart`) — no badge
  - Settings (`settings`) — no badge
- **"SAVED FILTERS" group** (heading uppercase 11px mono muted), styled like nav items with right-aligned counts:
  - `T1 · Remote · Not reviewed` (8)
  - `Staff PM · $200k+` (12)
  - `Snoozed > 7d` (4)
  - Click applies the saved filter to the main view (verified on Triage — clicked row visually highlights/bolds and main view re-filters). The filter row stays highlighted until another is selected.
- **Sidebar footer:** small status row, left = colored dot + `synced 14s ago` (11px mono muted, time string frequently changing), right = `⌘K` kbd chip. 1px top border separator.
- **Collapsed state** (toggle via banner sidebar-toggle button):
  - Brand block reduces to single-letter mark `J` (no version line)
  - Nav items show only icons (labels and badges both hidden)
  - "SAVED FILTERS" section hidden entirely
  - Footer collapses to just the status dot, no text and no ⌘K hint
- Toggle keyboard shortcut: none observed.

### Command palette (Jump to… / ⌘K modal)

- Trigger: `⌘K` / `Ctrl+K` globally, OR click "Jump to…" in the banner.
- Modal opens centered with `shadow-2xl`. No title — search input is the first element.
- Search input placeholder: `Search commands, jump to page…`
- **Default visible items before typing** (single flat list, no section headers, in order):
  - `Go to Triage` — shortcut hint `G T`
  - `Go to Applied` — shortcut hint `G A`
  - `Go to Pipeline` — shortcut hint `G P`
  - `Go to Companies` — shortcut hint `G C`
  - `Go to Outreach` — shortcut hint `G O`
  - `Go to Stats` — shortcut hint `G S`
  - `Go to Settings` — **no shortcut hint shown** (intentional or omission in live build — TBD)
  - `Run discover-ats` — type tag `job`
  - `Run Gmail backfill` — type tag `job`
  - `Run Greenhouse ingestion` — type tag `job`
  - `Rotate API keys` — type tag `system`
  - `Export postings as CSV` — type tag `data`
- **Filter behavior:** substring match, case-insensitive. Not fuzzy ("ppln" does not match "Pipeline"; "pipe" or "go p" does).
- **Result item structure:** leading `→` arrow glyph + label + right-aligned chip (shortcut hint `G X` rendered as two key chips with space, OR type tag `job` / `system` / `data` rendered as small uppercase tag).
- **Keyboard navigation:** ↑ / ↓ moves selection · Enter activates (verified) · Esc closes · click outside also closes.
- **Empty search state** (typed gibberish like "zzzzzzz"): centered text "No matches". No icon, no body copy.
- **Modal footer** (always visible): thin status bar — left = three labeled key chips `↑↓ navigate`, `↵ select`, `esc close`; right = static label `job-assist`.

### Top page banner pattern

Every page renders the same banner shape: sidebar-toggle button (left) → page title + subtitle → `Jump to…` button (right with ⌘K chip). Per-page additions appended to the right of `Jump to…`:
- Triage: inline keyboard legend `J / K nav · 1-4 act · 2 → 1-7 reason`
- Companies: `+ Add company` button
- Outreach: small static `BETA` tag

Pipeline, Stats, Settings have no banner adornments beyond the standard set.

### Toast / notification pattern

- **Placement:** bottom-right corner.
- **Surface:** single-line pill-shaped toast, rounded, leading `✓` glyph for success + message text.
- **Copy pattern:** `{verb-phrase} · {detail}`. Observed:
  - `✓ Profile saved · vector rewritten` (Settings → Save profile)
  - `✓ Rules applied · dropped 4 · surfaced 4` (Settings → Apply & refresh queue)
- **Duration:** ~2–3s auto-dismiss. No manual dismiss control.
- **Stacking:** only single toasts observed; multi-toast behavior unverified.
- **Coverage:** ONLY observed on Settings interactions in the current build. Outreach actions (Mark sent / Skip / Approve / Edit) mutate state inline without toasts. Companies note editing and close/reopen also produce no toast. Either the live build is silent on those flows, OR they fire toasts that were missed in the harness. **Port-time decision:** standardize toast usage across all mutating actions.

### Empty-state pattern

Three distinct shapes, each consistent within its category but visually distinct across:

1. **Full-page empty** (centered icon + heading + body) — used when a page-level data set is entirely empty.
   - Observed only on Outreach: chat-bubble icon, heading "No drafts queued", body explaining cadence.
2. **Inline placeholder** (muted italic token) — used for empty cells inside data tables.
   - `—` (em dash) for empty Pipeline columns, empty Companies outcomes cells.
   - `add note…` for empty Companies notes cells (also serves as affordance to open inline editor).
3. **Empty-search inside modal** (centered text) — used only in the command palette.
   - `No matches`. No icon, no body.

### Loading skeleton pattern

**No skeleton pattern observed.** All pages hydrate synchronously in the Lovable preview (hard-refresh produces no flash of skeleton / shimmer / placeholder). The only "loading-like" surface anywhere is the per-row `running…` label on Settings → Manual job triggers when a request is in flight.

**Port-time consideration:** for the real backend on Railway, network latency to load `GET /postings` with 1153+ rows may surface this gap. Decide whether to add skeletons for the initial Triage load, or rely on instant-skeleton-less hydration with progressive enhancement.

### Button hierarchy

- **Primary action:** filled background in accent color (light teal), white/dark text. Examples: `Save profile`, `Save hard rules` (when dirty), `Apply & refresh queue`, `+ Add company`, `Tune surfacing →` (Triage), `Approve` (Outreach, when toggled active).
- **Secondary action:** outlined, transparent bg, neutral text. Examples: `Mark sent`, `Edit`/`Done`, `Skip`, `Cancel` (modals), `run` (Settings job rows), unselected segmented options, `Add` (Closed channels), `reset`, the `−`/`+` steppers.
- **Destructive action:** **no fully-styled destructive button** observed by default. Row-level "remove" actions render as muted labels that adopt a red tint on hover / first-click (Companies `close`, chip `×`). **Port-time decision:** consider adopting an explicit destructive style for clarity.
- **Toggleable / active state:** outlined button gains filled / highlighted bg when active. Examples: `Approve` toggle, segmented Light/Dark, saved-filter rows in sidebar, `Save hard rules` (dirty state).
- **Icon-only:** borderless glyph, no chrome. Sidebar toggle, command-palette `Open JD`, chip / row / modal `×`, Triage card action icons.

### Keyboard shortcut conventions

- **`⌘K` / `Ctrl+K`** — open command palette. Global. Hint rendered as `⌘K` chip in both the banner "Jump to…" button and the sidebar footer.
- **`Esc`** — close command palette, close inline editors (verified on Companies note editor).
- **↑ / ↓** — move selection in command palette.
- **Enter** — activate selected command palette item.
- **`G T` / `G A` / `G P` / `G C` / `G O` / `G S`** — displayed as chord-style hints in the command palette but **NOT currently wired as global shortcuts** in the live build. Typing the chord with focus on the page body did not navigate. They appear to be advertised hints that aren't functional yet, or require a different invocation. **`Go to Settings` notably has no chord hint shown** (asymmetry possibly intentional, possibly omission). **Port-time decision:** wire the chord shortcuts globally with a prefix-key-plus-timeout hook (mentioned in the original spec's port notes), OR strip the hints if the feature isn't shipping in v1.
- **Triage-specific keys** (`J` / `K` / `1`–`4` / `2 → 1`–`7`) — see Triage section. These ARE wired and functional in the live build.

### Footer / status line

- **Sidebar status row** (`• synced 14s ago` left, `⌘K` chip right): **global** — present on every page.
- **Page-level `contentinfo` footer** with build info (`job-assist · build 0.4.0 · api railway-prod · last sync 14s ago`): **only on Settings**. Verified by DOM inspection on Companies (no `<footer>` / `[role="contentinfo"]` element).

---

## Notes for the Next.js port

### Styling and tokens

- Keep oklch values exact — don't approximate to hex when defining theme tokens.
- Tailwind config needs custom color tokens for: `surface`, `surface-2`, `border-strong`, `positive`, `negative`, `pending`, `tier-1` through `tier-4`, `ats-greenhouse`, `ats-lever`, `ats-ashby`.
- Lucide-react is the icon library.
- Use `next/font/google` for both Inter and JetBrains Mono.
- All badges follow `bg-{color}/15 text-{color} ring-1 ring-inset ring-{color}/30` pattern.
- Light theme is default; dark theme accessible via Settings appearance toggle. Persist via localStorage.

### Behavior patterns

- Two-key keyboard sequences (`G T`, etc.) need a custom hook listening for the prefix key + timeout. Currently NOT wired in the live build — port can either ship them or strip the hints.
- Reason picker inline expansion (Triage) is a state-driven swap, not a popover library.
- Command palette is `⌘K`-driven — likely `cmdk` library or shadcn/ui Command component.
- Companies notes use inline single-line text input with Escape-to-cancel.
- Settings → Hard rule thresholds slider previews require a live-recompute endpoint or pre-computed cohort to power the "Would drop N" lines without round-tripping.

### Backend prerequisites identified during capture

Three backend gaps surfaced that PR #32 needs to resolve before the corresponding UI surfaces can be functional, not just present:

1. **Companies notes** — `target_company.notes` column + PATCH endpoint do not exist. Either strip from v1 UI or land a small pre-req PR.
2. **Stats "SOURCE EFFECTIVENESS" panel** — not covered by PR #30b's `/stats/funnel` or `/stats/calibration`. Either strip from v1 UI or extend `/stats/funnel` (or add a dedicated endpoint).
3. **Companies "+ Add company" button** — unwired in live build. Either hide for v1 or add a small POST endpoint for target_company creation. The target list is operator-curated today, so hiding is reasonable for v1.

### Other port-time decisions to make

- **Pipeline card click behavior** — currently inert in live build. Decide: open Triage-style detail panel, navigate to `/postings/{id}`, or leave static.
- **Skeleton loading states** — live build has none; real backend latency may warrant them. Decide which pages need them (Triage with 1000+ postings most likely).
- **Destructive button style** — no styled destructive button exists in current build. Decide whether to standardize one.
- **Toast coverage** — currently only Settings produces toasts. Decide whether to standardize across all mutating actions (mark sent, applied, rejected, etc.) or keep silent-inline elsewhere.
- **Chord shortcuts (`G T` etc.)** — wire them or strip the hints. Don't ship a half-state.
