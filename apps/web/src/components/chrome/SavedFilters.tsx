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
 * would over-highlight, so we compare full param sets.
 *
 * PR #72: switched from raw ``toString()`` string-equality to a
 * normalized lexicographic comparator. The old check failed on
 * "T1+T2 · PM" (URL ``?tier=1&tier=2&role_family=...&state=triage``)
 * because multi-value keys are insertion-order-sensitive — any
 * reordering by the router or a future re-emit broke equality.
 * Sorting key+value pairs makes the comparison order-independent
 * while still preserving multi-value duplicates (distinct pairs).
 *
 * Hidden entirely when the sidebar is collapsed, per UI_SPEC.md.
 */
export function SavedFilters({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname();
  const search = useSearchParams();
  if (collapsed) return null;

  const currentNormalized = normalizedQuery(search.toString());
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
            active={pathname === '/' && currentNormalized === normalizedQuery(searchOf(f.href))}
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

/**
 * Normalize a query string for order-independent equality. Sorts
 * ``[key, value]`` pairs lexicographically (by key, then value) and
 * re-joins. Multi-value duplicates survive because they're distinct
 * pairs in the array. Empty input yields the empty string.
 *
 * Examples:
 *   "tier=2&tier=1" → "tier=1&tier=2"
 *   "state=triage&tier=1" === "tier=1&state=triage" (after normalize)
 */
function normalizedQuery(qs: string): string {
  if (!qs) return '';
  const sp = new URLSearchParams(qs);
  const pairs: [string, string][] = [];
  sp.forEach((v, k) => pairs.push([k, v]));
  pairs.sort((a, b) => a[0].localeCompare(b[0]) || a[1].localeCompare(b[1]));
  return pairs.map(([k, v]) => `${k}=${v}`).join('&');
}
