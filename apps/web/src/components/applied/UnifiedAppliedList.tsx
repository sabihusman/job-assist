'use client';

import { useMemo } from 'react';

import { UnifiedAppliedRow } from '@/components/applied/UnifiedAppliedRow';
import { useAllOutcomes } from '@/lib/api/applied';
import type { AppliedSort } from '@/lib/applied/types';
import { sortUnified, unifyApplied } from '@/lib/applied/unify';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Unified Applied list (feat/applied-unified). Fuses the manual Applied funnel
 * (`manualPostings`, ~4) with the full Gmail-detected history
 * (`useAllOutcomes(job_related)`, ~150) via `unifyApplied`, then sorts. The
 * Gmail set is shared with Pipeline/Companies so navigating between them
 * doesn't refetch.
 */
export function UnifiedAppliedList({
  manualPostings,
  sort,
}: {
  manualPostings: readonly PostingListItem[];
  sort: AppliedSort;
}) {
  // job_related=true mirrors the Pipeline: lifecycle outcomes only (drops the
  // classifier's ~1,700 unrelated/unclassified noise rows).
  const { data: outcomes } = useAllOutcomes(true);

  const entries = useMemo(
    () => sortUnified(unifyApplied(outcomes?.items ?? [], manualPostings), sort),
    [outcomes, manualPostings, sort],
  );

  if (entries.length === 0) {
    return (
      <section
        data-testid="applied-empty"
        className="flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
      >
        <h2 className="text-sm font-semibold">No applications yet.</h2>
        <p className="text-[13px] text-muted-foreground">
          Applications detected from Gmail and ones you mark Applied in Triage show up here.
        </p>
      </section>
    );
  }

  return (
    <ul className="flex list-none flex-col gap-3 p-0">
      {entries.map((entry) => (
        <UnifiedAppliedRow key={entry.key} entry={entry} />
      ))}
    </ul>
  );
}
