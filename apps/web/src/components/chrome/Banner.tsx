'use client';

import { PanelLeft, Search } from 'lucide-react';
import type { ReactNode } from 'react';

import { useUiStore } from '@/lib/stores/ui';
import { cn } from '@/lib/utils';

/**
 * 48px sticky banner. Layout (left-to-right):
 *   [sidebar toggle] [title / subtitle] ...... [Jump to…] [adornments]
 *
 * `adornments` is the per-page right-side slot. In #32a no page uses
 * it — the Triage J/K legend lands in #32b, and Companies' "+ Add
 * company" button was stripped from v1.
 *
 * `bg-surface/80` + `backdrop-blur` produces the slight translucency
 * UI_SPEC.md flags. Sticky positioning keeps it pinned as the main
 * content scrolls underneath.
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
  const openPalette = useUiStore((s) => s.openPalette);

  return (
    <header
      className={cn(
        'sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur',
      )}
    >
      <button
        type="button"
        onClick={toggleSidebar}
        aria-label="Toggle sidebar"
        className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      >
        <PanelLeft className="h-4 w-4" />
      </button>

      <div className="flex min-w-0 flex-col leading-tight">
        <h1 className="truncate text-[14px] font-semibold">{title}</h1>
        {subtitle && <p className="truncate text-[13px] text-muted-foreground">{subtitle}</p>}
      </div>

      <div className="ml-auto flex items-center gap-3">
        <button
          type="button"
          onClick={openPalette}
          aria-label="Open command palette"
          className={cn(
            'flex h-8 w-[280px] items-center gap-2 rounded-md border border-border bg-surface-2 px-3 text-left text-sm text-muted-foreground transition-colors',
            'hover:border-border-strong',
          )}
        >
          <Search className="h-3.5 w-3.5" />
          <span className="flex-1 truncate">Jump to…</span>
          <kbd className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-[10px]">
            ⌘K
          </kbd>
        </button>
        {adornments}
      </div>
    </header>
  );
}
