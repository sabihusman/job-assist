'use client';

import { Menu, PanelLeft, Search } from 'lucide-react';
import type { ReactNode } from 'react';

import { useUiStore } from '@/lib/stores/ui';
import { cn } from '@/lib/utils';

/**
 * 48px sticky banner. Layout (left-to-right):
 *   [mobile hamburger | desktop sidebar toggle]
 *   [title / subtitle]
 *   ......
 *   [Jump to… | search-icon-only at <sm]
 *   [adornments]
 *
 * PR 1 UX overhaul:
 *   - Hamburger at < md (44×44px tap target) opens the mobile drawer.
 *     Desktop's PanelLeft toggle is hidden at < md.
 *   - The ⌘K search trigger collapses to an icon-only square at < sm
 *     (where the 280px wide bar would crowd out the title).
 *
 * ``bg-surface/80`` + ``backdrop-blur`` produces the slight
 * translucency UI_SPEC.md flags. Sticky positioning keeps it pinned
 * as the main content scrolls underneath.
 */
export function Banner({
  title,
  subtitle,
  adornments,
}: {
  title: string;
  subtitle?: string;
  adornments?: ReactNode;
}) {
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const openSidebarMobile = useUiStore((s) => s.openSidebarMobile);
  const openPalette = useUiStore((s) => s.openPalette);

  return (
    <header
      className={cn(
        'sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur',
      )}
    >
      {/* Mobile hamburger — visible only < md. 44px min tap target. */}
      <button
        type="button"
        onClick={openSidebarMobile}
        aria-label="Open navigation menu"
        className="flex h-11 w-11 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground md:hidden"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Desktop sidebar toggle — visible only ≥ md (mobile uses the
          hamburger above, which opens the drawer rather than collapsing). */}
      <button
        type="button"
        onClick={toggleSidebar}
        aria-label="Toggle sidebar"
        className="hidden h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground md:flex"
      >
        <PanelLeft className="h-4 w-4" />
      </button>

      {/* PR 2 UX overhaul: dropped ``truncate`` from the subtitle. Pre-PR-2,
          "716 pending · 0 applied" clipped to "716 pending · 0 ..." at every
          viewport because ``min-w-0`` + ``truncate`` let the parent shrink
          below content width while the 280px ⌘K bar took the rest. The
          natural width of the longest expected subtitle is ~140px — plenty
          of room next to a 280px search bar at the viewports where that
          search bar is even shown (≥sm). ``min-w-0`` stays on the container
          so very long pending counts still allow other siblings to shrink
          first. Title keeps ``truncate`` defensively. */}
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <h1 className="truncate text-md font-semibold">{title}</h1>
        {subtitle && <p className="text-base text-muted-foreground">{subtitle}</p>}
      </div>

      <div className="ml-auto flex items-center gap-3">
        {/* Icon-only search trigger at < sm. */}
        <button
          type="button"
          onClick={openPalette}
          aria-label="Open command palette"
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface-2 text-muted-foreground transition-colors',
            'hover:border-border-strong sm:hidden',
          )}
        >
          <Search className="h-4 w-4" />
        </button>

        {/* Full search trigger at ≥ sm. */}
        <button
          type="button"
          onClick={openPalette}
          aria-label="Open command palette"
          className={cn(
            'hidden h-8 w-[280px] items-center gap-2 rounded-md border border-border bg-surface-2 px-3 text-left text-md text-muted-foreground transition-colors',
            'hover:border-border-strong sm:flex',
          )}
        >
          <Search className="h-3.5 w-3.5" />
          <span className="flex-1 truncate">Jump to…</span>
          <kbd className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-2xs">
            ⌘K
          </kbd>
        </button>
        {adornments}
      </div>
    </header>
  );
}
