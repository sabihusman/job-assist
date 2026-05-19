'use client';

import { useEffect, useState } from 'react';

import { SavedFilters } from '@/components/chrome/SavedFilters';
import { SidebarItem } from '@/components/chrome/SidebarItem';
import { NAV_ITEMS } from '@/components/chrome/nav-items';
import { useUiStore } from '@/lib/stores/ui';
import { cn } from '@/lib/utils';

/**
 * Left rail. Expanded = 224px (`w-56`), collapsed = ~52px just the
 * icons. Brand block, primary nav, SAVED FILTERS, sync-status footer.
 *
 * `mounted` gate: the persisted `sidebarCollapsed` reads from
 * localStorage, which doesn't exist server-side. To avoid a hydration
 * mismatch (server renders expanded, client snaps to collapsed) we
 * render the expanded default until the client has had a tick to
 * rehydrate the zustand store.
 */
export function Sidebar() {
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Always render expanded on the server / first paint, then honor the
  // persisted choice.
  const collapsed = mounted && sidebarCollapsed;

  return (
    <aside
      data-collapsed={collapsed}
      className={cn(
        'sticky top-0 z-30 flex h-screen shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150',
        collapsed ? 'w-[52px]' : 'w-56',
      )}
      aria-label="Primary navigation"
    >
      {/* Brand */}
      <div className="flex h-12 items-center gap-2 px-3">
        <div
          aria-hidden="true"
          className="flex h-8 w-8 items-center justify-center rounded-md bg-primary font-semibold text-primary-foreground"
        >
          J
        </div>
        {!collapsed && (
          <div className="flex flex-col leading-tight">
            <span className="text-[12px] font-bold uppercase tracking-wide">Job Assist</span>
            <span className="font-mono text-[11px] text-muted-foreground">v0.3 · local</span>
          </div>
        )}
      </div>

      {/* Primary nav */}
      <nav aria-label="Primary" className="mt-3 flex flex-col gap-0.5 px-2">
        {NAV_ITEMS.map((item) => (
          <SidebarItem
            key={item.href}
            href={item.href}
            label={item.label}
            icon={item.icon}
            badge={item.badge}
            collapsed={collapsed}
          />
        ))}
      </nav>

      {/* SAVED FILTERS */}
      <div className="px-2">
        <SavedFilters collapsed={collapsed} />
      </div>

      {/* Sync footer */}
      <div className="mt-auto border-t border-border px-3 py-2">
        <SyncStatus collapsed={collapsed} />
      </div>
    </aside>
  );
}

/**
 * Sidebar footer status row. UI_SPEC.md describes a "synced 14s ago"
 * relative timer + `⌘K` chip on the right. No `/healthz` endpoint
 * exists yet, so we tick from page-load time — close enough for v1
 * and replaceable by a real liveness ping later.
 */
function SyncStatus({ collapsed }: { collapsed: boolean }) {
  const [secondsAgo, setSecondsAgo] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const interval = setInterval(
      () => setSecondsAgo(Math.floor((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(interval);
  }, []);

  if (collapsed) {
    return (
      <div className="flex justify-center">
        <span
          aria-label={`synced ${secondsAgo}s ago`}
          className="inline-block h-2 w-2 rounded-full bg-positive"
        />
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between font-mono text-[11px] text-muted-foreground">
      <span className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 rounded-full bg-positive" />
        <span>synced {secondsAgo}s ago</span>
      </span>
      <kbd className="rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[10px] text-muted-foreground">
        ⌘K
      </kbd>
    </div>
  );
}
