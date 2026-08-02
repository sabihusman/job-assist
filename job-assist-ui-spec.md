# Job Assist UI Spec (from Lovable build)

Source: https://job-nest-dash.lovable.app/ (build label "job-assist · build 0.4.0 · api railway-prod"). Theme system is built on Tailwind + CSS custom properties with light (default) and dark variants. Light theme is described in the Settings page itself as "Light · warm off-white default".

> **Inventory pass note:** Claude in Chrome session that produced this had only browser tools — no shell/file-write — so the spec was returned inline. The Pipeline / Companies / Outreach / Stats / Settings sections were cut off mid-capture (token budget). Re-run the inventory pass against those pages before writing the Next.js port PR; do not assume completeness for any section after "Applied".

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
  - Page title H1 ("Triage", "Applied"): **14px / 600** — paired with 13px muted subtitle.
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

### Header (banner)

- Sticky top, 48px tall, white surface, 1px bottom border (`border-border`), background with slight translucency (`oklab(0.985 ... / 0.9)`).
- **Left cluster:** sidebar-toggle icon button (small ghost) + page title H1 (`14px / 600`) + muted subtitle (e.g. "22 pending · 5 applied", "5 active applications", "Kanban by outcome stage", "16 target companies · 1 closed", "3 pending drafts · Week 3 preview", "Operator metrics", "Operator tuning interface · single-user").
- **Center:** "Jump to…" search button. Width ~280–320px, `bg-surface`, 1px `border-border`, `rounded`, magnifying-glass icon left, `⌘K` kbd chip right (mono 10px on `bg-surface-2` pill). Opens command palette.
- **Right meta strip** (Triage only): mono helpline `J / K nav · 1-4 act · 2 → 1-7 reason` with each key as `<kbd>` chip (small rounded `bg-surface-2`, 1px border, 10–11px mono, padding 2px 5px).
- Outreach page header has a small **"BETA"** pill (orange/amber) on the right.
- Companies page header has a `+ Add company` button (ghost-bordered) on the right.

### Sidebar (left nav)

- 224px wide, full height, `bg-surface`, 1px right border. Sticky.
- **Top brand block:** 32×32 rounded-square teal "J" tile (`bg-primary` with white "J"), two-line wordmark:
  - Line 1: `JOB ASSIST` (uppercase, bold, 12–13px tracking-wide)
  - Line 2: `v0.3 · local` (mono, muted, ~11px)
- **Nav items:** vertical list, each row = icon (lucide) + label, padding ~6–8px vertical / ~12px horizontal, full-width, `rounded` on active row using `bg-accent` with foreground text. Inactive: muted-foreground, hover `bg-accent/50`. Counts appear as small mono 10–11px pill on right (`bg-surface-2` / muted).
  - Triage (lucide `mail`/inbox icon, count `24`)
  - Applied (`activity`)
  - Pipeline (`columns`/`kanban-square`)
  - Companies (`building`)
  - Outreach (`message-square`, count `3`)
  - Stats (`bar-chart`)
  - Settings (`settings`)
- **"SAVED FILTERS"** group label (uppercase 11px mono muted), then saved-filter buttons styled like nav items with right-aligned counts:
  - `T1 · Remote · Not reviewed` (8)
  - `Staff PM · $200k+` (12)
  - `Snoozed > 7d` (4)
- **Footer:** tiny status row, left = `• synced 14s ago` (green dot + 11px mono muted), right = `⌘K` chip. 1px top border separator.

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
- `G T / G A / G P / G C / G O / G S` — go to Triage/Applied/Pipeline/Companies/Outreach/Stats (two-key sequence)
- `esc` — close reason picker / dialog

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

## Sections incomplete in inventory — re-capture before porting

The Claude in Chrome inventory pass was truncated mid-page. The following routes were either started or skipped and need a fresh inventory before the Next.js port PR:

- **Pipeline (`/pipeline`)** — kanban-style view of applications by outcome stage. Only title and column-header typography captured (`11px / 400` mono uppercase tracking-wider muted; count pill 10px mono on `bg-surface-2`). Column-by-column layout, card content, drag/drop behavior, empty-column state — all uncaptured.
- **Companies (`/companies`)** — table view of target_company rows. Only the `+ Add company` header button was noted. Table columns, row actions, notes-editing affordance, mark-as-closed-channel flow — all uncaptured.
- **Outreach (`/outreach`)** — Week 3 preview. Only the "BETA" pill in the header was noted. List of pending drafts, approve/edit/skip flow, draft text layout — all uncaptured.
- **Stats (`/stats`)** — operator metrics. Only the KPI display sizes were noted (28–32px, weight 700, for numbers like "312", "1,184", "34%"). Chart types, funnel layout, breakdowns — all uncaptured.
- **Settings (`/settings`)** — Only the single-scroll layout + bottom footer line `job-assist · build 0.4.0 · api railway-prod · last sync 14s ago` were noted. The full set of sections (Profile, Hard-rule thresholds with sliders, API-key dots, Manual job triggers, Appearance toggle, "What I'm looking for right now" textarea) — all uncaptured.

**Components inventory** (the full list of reusable components with props/variants/states), **empty + loading states** beyond what's noted above, and **Lovable-specific portability notes** — all uncaptured. Should be produced as part of the re-capture.

---

## Notes for the Next.js port

- Keep oklch values exact — don't approximate to hex when defining theme tokens.
- Tailwind config needs custom color tokens for: `surface`, `surface-2`, `border-strong`, `positive`, `negative`, `pending`, `tier-1` through `tier-4`, `ats-greenhouse`, `ats-lever`, `ats-ashby`.
- Lucide-react is the icon library.
- Use `next/font/google` for both Inter and JetBrains Mono.
- Two-key keyboard sequences (`G T`, etc.) need a custom hook listening for the prefix key + timeout.
- Reason picker inline expansion is a state-driven swap, not a popover library.
- Command palette is `⌘K`-driven — likely `cmdk` library or shadcn/ui Command component.
- All badges follow `bg-{color}/15 text-{color} ring-1 ring-inset ring-{color}/30` pattern.
- Calibration card is mocked data — backend endpoint (`/stats/calibration`) needs to be built before this is real.
- Light theme is default; dark theme accessible via Settings appearance toggle. Persist via localStorage.
