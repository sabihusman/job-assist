'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';

import { SAVED_FILTERS } from '@/components/chrome/nav-items';
import { cn } from '@/lib/utils';

/**
 * Hardcoded SAVED FILTERS section. Each row links to `/?filter={slug}`
 * — #32b will translate the slug into real `?state=...&tier=...` query
 * params on the Triage page.
 *
 * Hidden entirely when the sidebar is collapsed, per UI_SPEC.md.
 */
export function SavedFilters({ collapsed }: { collapsed: boolean }) {
  const params = useSearchParams();
  const currentFilter = params.get('filter');

  if (collapsed) return null;

  return (
    <div className="mt-6">
      <h2 className="px-2 pb-2 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        Saved filters
      </h2>
      <nav aria-label="Saved filters" className="flex flex-col gap-0.5">
        {SAVED_FILTERS.map((f) => {
          const active = currentFilter === f.slug;
          return (
            <Link
              key={f.slug}
              href={`/?filter=${f.slug}`}
              data-active={active}
              className={cn(
                'flex h-8 items-center rounded-md px-2 text-sm transition-colors',
                'hover:bg-accent hover:text-accent-foreground',
                active ? 'bg-accent text-accent-foreground font-medium' : 'text-foreground/80',
              )}
            >
              <span className="flex-1 truncate">{f.label}</span>
              <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                {f.count}
              </span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
