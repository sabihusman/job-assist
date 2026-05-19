'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';

import { SAVED_FILTERS, type SavedFilter } from '@/components/chrome/nav-items';
import { useSavedFilterCount } from '@/lib/api/hooks';
import { cn } from '@/lib/utils';

/**
 * Sidebar SAVED FILTERS section. Each row deep-links to Triage with
 * preset query params; the count badge is a live `useSavedFilterCount`
 * query against `GET /postings?…&limit=1` (5-minute staleness, see
 * `useSavedFilterCount` for the budget).
 *
 * Active row highlighting matches when the current pathname is `/` AND
 * the full query-string matches the saved filter's encoded params —
 * not just the slug. Loose path-equality (e.g. matching just `tier`)
 * would over-highlight, so we string-compare.
 *
 * Hidden entirely when the sidebar is collapsed, per UI_SPEC.md.
 */
export function SavedFilters({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname();
  const search = useSearchParams();
  if (collapsed) return null;

  const currentSearch = search.toString();
  return (
    <div className="mt-6">
      <h2 className="px-2 pb-2 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        Saved filters
      </h2>
      <nav aria-label="Saved filters" className="flex flex-col gap-0.5">
        {SAVED_FILTERS.map((f) => (
          <SavedFilterRow
            key={f.slug}
            filter={f}
            active={pathname === '/' && currentSearch === searchOf(f.href)}
          />
        ))}
      </nav>
    </div>
  );
}

function SavedFilterRow({
  filter,
  active,
}: {
  filter: SavedFilter;
  active: boolean;
}) {
  const { data, isLoading, isError } = useSavedFilterCount(filter.filterParams);
  const badge = isLoading ? '…' : isError ? '—' : String(data ?? 0);

  return (
    <Link
      href={filter.href}
      data-active={active}
      aria-label={filter.label}
      className={cn(
        'flex h-8 items-center rounded-md px-2 text-sm transition-colors',
        'hover:bg-accent hover:text-accent-foreground',
        active ? 'bg-accent text-accent-foreground font-medium' : 'text-foreground/80',
      )}
    >
      <span className="flex-1 truncate">{filter.label}</span>
      <span
        aria-hidden="true"
        className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
      >
        {badge}
      </span>
    </Link>
  );
}

function searchOf(href: string): string {
  const idx = href.indexOf('?');
  return idx >= 0 ? href.slice(idx + 1) : '';
}
