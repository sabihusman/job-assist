'use client';

import { useId } from 'react';

import { DEFAULT_SORT, type SortKey } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * Sort dropdown for the Triage list (PR #49).
 *
 * Native `<select>` for keyboard/a11y for free. The styling matches the
 * existing filter chips: surface bg, 1px ring, small text, mono uppercase
 * label. Five options, default "Newest" — see SortKey in lib/triage/types.ts
 * for the wire vocabulary contract with the backend.
 */

// Wire key → operator-facing label. Order in this list = order in the
// rendered <select>. Default (newest) first.
const SORT_OPTIONS: readonly { wire: SortKey; label: string }[] = [
  { wire: 'newest', label: 'Newest' },
  { wire: 'oldest', label: 'Oldest' },
  { wire: 'salary_high_to_low', label: 'Salary high to low' },
  { wire: 'tier', label: 'Tier' },
  { wire: 'recently_posted', label: 'Recently posted' },
  // PR #57: "Best fit" reads fit_score DESC NULLS LAST. NULL-score
  // postings sink to the bottom — operator scrolls until the score
  // drops below their personal threshold (no explicit filter yet).
  { wire: 'best_fit', label: 'Best fit' },
  // Slice 2b: blends fit_score with calibrated similarity behind the operator's
  // Semantic weight (Settings); at weight 0 this is identical to Best fit.
  { wire: 'best_fit_semantic', label: 'Best fit (semantic)' },
] as const;

export function SortDropdown({
  value,
  onChange,
}: {
  value: SortKey;
  onChange: (next: SortKey) => void;
}) {
  const selectId = useId();
  // Defensive: if a stale URL hands us a value outside the enum, the
  // parser already substituted DEFAULT_SORT — but render-time guard
  // means the <select> never enters an "out of range" state.
  const safeValue: SortKey = SORT_OPTIONS.some((o) => o.wire === value) ? value : DEFAULT_SORT;

  return (
    <div className="flex items-center gap-2">
      <label
        htmlFor={selectId}
        className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground"
      >
        SORT
      </label>
      <select
        id={selectId}
        value={safeValue}
        onChange={(e) => onChange(e.target.value as SortKey)}
        className={cn(
          'rounded bg-surface px-2 py-0.5 text-xs ring-1 ring-inset ring-border',
          'text-muted-foreground hover:text-foreground',
          'focus:outline-none focus:ring-2 focus:ring-ring',
        )}
      >
        {SORT_OPTIONS.map((opt) => (
          <option key={opt.wire} value={opt.wire}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}
