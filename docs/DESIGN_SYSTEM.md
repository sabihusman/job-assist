# Design system

> Foundation laid in PR 1 of the UX overhaul. Pages migrate to these tokens and primitives incrementally across PR 2 (Triage) and PR 3 (the other 8 pages).

Linear-flavored: monochrome chrome surfaces, dense information layout, semibold accents only where they encode product meaning. Sharp on small elements, slightly rounded on cards.

---

## Color tokens

All colors are oklch values exposed as CSS variables in `apps/web/src/app/globals.css`. Light + dark both fully defined. Tailwind utilities (`bg-foo`, `text-foo`, etc.) wrap the variables with `oklch(var(--foo) / <alpha-value>)` so `bg-primary/30` still works.

### Chrome (monochrome)

Use these for every layout surface, border, and non-semantic text. Anchored around a near-neutral base in light mode, near-black in dark.

| Token | Use |
|---|---|
| `background` | Page background |
| `foreground` | Default text |
| `surface` | Banner, Sidebar, Card |
| `surface-2` | Subtler nested surface (saved-filter pills, ⌘K trigger) |
| `card` / `card-foreground` | Card primitive surface |
| `muted` / `muted-foreground` | De-emphasized text (meta labels, hints) |
| `border` / `border-strong` | Standard / hover borders |
| `input` | Form-control fill |
| `popover` / `popover-foreground` | Dialog, Sheet, command palette |
| `accent` / `accent-foreground` | Hover, active row |
| `ring` | Focus outline |

### Semantic state

Used to encode meaning, not chrome decoration. Don't reach for these to color a button without a reason.

| Token | Meaning |
|---|---|
| `primary` | Default brand action |
| `positive` | Success, ✓ Applied, "synced" |
| `negative` | Destructive, "Couldn't load…" cards |
| `pending` | "Paused" badge, "Snoozed" hint |
| `destructive` | Hard delete / dangerous |

### Product-semantic (DO NOT replace with monochrome)

These encode product data — touching them changes meaning, not just appearance. Treat them as exceptions to the monochrome chrome treatment.

| Token | Encodes |
|---|---|
| `tier-1` / `tier-2` / `tier-3` / `tier-4` | Company priority tier (UI_SPEC.md) |
| `ats-greenhouse` / `ats-lever` / `ats-ashby` | ATS brand identity (badges in Triage / Companies) |

---

## Typography

Inter (variable) for sans, JetBrains Mono for mono. Loaded via `next/font` in `apps/web/src/app/layout.tsx`, exposed as `--font-inter` / `--font-jetbrains-mono`, mapped to Tailwind's `font-sans` / `font-mono`.

### Density scale (PR 1)

Defined in `apps/web/tailwind.config.ts` under `theme.extend.fontSize`. Names the pixel values that 189 arbitrary `text-[Npx]` call sites already use. Tighter than Tailwind's default scale — optimized for dense PM-tool data.

| Token | Size / line-height | Used for |
|---|---|---|
| `text-2xs` | 10 / 14 | Keyboard chips, badge counts, mono labels |
| `text-xs` | 11 / 16 | Section headers (font-mono uppercase), muted meta |
| `text-sm` | 12 / 18 | Filter chips, card row meta, sidebar labels |
| `text-base` | 13 / 20 | Default body, card titles, table cells |
| `text-md` | 14 / 20 | Banner title, primary nav, ⌘K trigger label |
| `text-lg` | 16 / 24 | Detail-panel hero title |
| `text-xl` | 18 / 26 | Page-level h2 (reserved for future use) |
| `text-2xl` | 24 / 32 | KPI numbers, hero stats |

**Important**: this scale REPLACES Tailwind's defaults for the overlapping names (`text-xs` was 12 → now 11, etc). The 54 existing named-token call sites tighten by 1–2px after this PR. The codebase was already moving this direction via arbitrary `text-[Npx]`; PR 1 aligns the named scale with that density target.

**Migration of arbitrary `text-[Npx]`**: deferred. Pages opportunistically swap `text-[11px]` → `text-xs` etc. as they're touched in PR 2/PR 3.

---

## Spacing & radius

| | Value | Notes |
|---|---|---|
| Spacing scale | Tailwind default (4px increment) | No customization in PR 1 |
| Border radius `lg` | 6px | `var(--radius)` — used by Card, Banner search trigger |
| Border radius `md` | 4px | Derived (`calc(var(--radius) - 2px)`) — buttons |
| Border radius `sm` | 2px | Derived — focus rings on inline chips |
| Focus ring | `ring-2 ring-ring ring-offset-2` | Wired in `globals.css *:focus-visible` |
| `shadow-card` | Faint elevation | Light: 1+4px subtle; dark: heavier blacks |

---

## Breakpoints (Tailwind defaults)

| Token | Min width | Used for |
|---|---|---|
| `sm` | 640px | ⌘K trigger label appears (icon-only below) |
| `md` | 768px | **Sidebar transitions: drawer → in-place**. Most layout responsiveness. |
| `lg` | 1024px | **Triage DetailPanel transitions: Sheet → in-place aside**. Two-column patterns on Settings. |
| `xl` | 1280px | unused |
| `2xl` | 1536px | unused |

---

## Dark mode

Class strategy — `darkMode: 'class'` in `tailwind.config.ts`, controlled by `next-themes`. Full token parity (every light variable has a dark counterpart). The `ThemeToggle` component lives at `components/chrome/ThemeToggle.tsx` but is not yet mounted in chrome — PR 1 wires dark mode without exposing a UI toggle.

---

## Layout primitives

### `AppShell` (`components/chrome/AppShell.tsx`)

Top-level wrapper. Composes Sidebar + Banner + main + CommandPalette. Every page renders `<AppShell title="…">{content}</AppShell>`.

Responsive: Sidebar is an in-place rail at ≥ md, off-canvas Sheet drawer at < md (triggered by Banner's hamburger).

### `Sidebar` (`components/chrome/Sidebar.tsx`)

Desktop (≥ md): in-place. Expanded = 224px, collapsed = 52px icon-only. State persisted to localStorage.

Mobile (< md): off-canvas drawer via Sheet, opened by Banner hamburger. Closes on backdrop tap and on nav-item click.

### `Banner` (`components/chrome/Banner.tsx`)

Sticky 48px header. Holds title/subtitle/adornments + ⌘K trigger.

Responsive: ⌘K trigger collapses to icon-only at < sm. Hamburger replaces PanelLeft toggle at < md.

### `EmptyState` (`components/shared/EmptyState.tsx`) — PR 1 new

Shared empty-state surface. Replaces 11 page-local implementations. Pages migrate in PR 2/PR 3.

```tsx
<EmptyState
  title="No passed postings yet."
  description="Postings you pass will land here."
  action={<button onClick={onReset}>Reset filters</button>}
  testId="passed-empty"
/>
```

### `Card` / `CardHeader` / `CardTitle` / `CardDescription` / `CardContent` / `CardFooter` (`components/ui/card.tsx`) — PR 1 new

shadcn-style Card primitive. Pages currently use inline `bg-card border-border rounded-md`; this primitive formalizes the contract. Adopted incrementally.

```tsx
<Card>
  <CardHeader>
    <CardTitle>Stats</CardTitle>
    <CardDescription>Last 7 days</CardDescription>
  </CardHeader>
  <CardContent>…</CardContent>
  <CardFooter>…</CardFooter>
</Card>
```

### `Sheet` (`components/ui/sheet.tsx`) — PR 1 new

Directional Dialog from Radix. Two consumers:
- Sidebar mobile drawer (`side="left"`)
- Triage DetailPanel mobile fallback (`side="bottom"`, full height)

Use `overlayClassName` to gate the backdrop responsively (e.g. `"lg:hidden"` if the Sheet itself is hidden at lg+).

---

## What PR 1 didn't ship

Folded into PR 2 or PR 3:

- **`PageHeader`**: Banner already serves this role; renaming would create churn for no semantic gain.
- **`DataList`**: YAGNI — Applied, Passed, Rejected, Contacts each render heterogeneous row shapes. A shared list wrapper would obscure the differences without simplifying enough call sites.
- **shadcn Button / Input / Select / Tooltip / Tabs**: pages roll their own; add per page only when the second consumer appears.
- **Font-size migration**: existing `text-[10px]` / `text-[11px]` / `text-[12px]` / `text-[13px]` call sites stay until pages are touched.
- **PageHeader breadcrumbs**: deferred to PR 3 if Settings nav grows.

## Naming conventions

- shadcn-style primitives live in `components/ui/`. forwardRef + displayName.
- Application-specific shared components live in `components/shared/`.
- Page-specific components live in `components/<feature>/`.
- Test files colocate next to the source: `Foo.tsx` + `Foo.test.tsx`.
