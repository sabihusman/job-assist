'use client';

import { useId } from 'react';

import { cn } from '@/lib/utils';

/**
 * Sort control for the Pipeline kanban. Sorts each column independently by the
 * card's outcome/email date (`appliedAt`), client-side — the card set is fully
 * loaded (usePipelineData), so no backend sort param is needed. Mirrors
 * triage/SortDropdown styling (native <select> for free keyboard/a11y).
 */

export type PipelineSort = 'recent' | 'oldest';

export const DEFAULT_PIPELINE_SORT: PipelineSort = 'recent';

const SORT_OPTIONS: readonly { wire: PipelineSort; label: string }[] = [
  { wire: 'recent', label: 'Most recent' },
  { wire: 'oldest', label: 'Oldest' },
] as const;

export function PipelineSortControl({
  value,
  onChange,
}: {
  value: PipelineSort;
  onChange: (next: PipelineSort) => void;
}) {
  const selectId = useId();
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
        value={value}
        onChange={(e) => onChange(e.target.value as PipelineSort)}
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
