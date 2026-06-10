'use client';

import { useMemo } from 'react';

import { UnifiedAppliedRow } from '@/components/applied/UnifiedAppliedRow';
import { useAllOutcomes } from '@/lib/api/applied';
import type { AppliedSort } from '@/lib/applied/types';
import { entryStage, sortUnified, unifyApplied } from '@/lib/applied/unify';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Unified Rejected list (feat/rejected-unified) — the exact mirror of
 * UnifiedAppliedList for the rejected stage. Fuses Gmail-detected rejections
 * (Pipeline REJECTED stage) with manually-rejected postings via the SAME
 * `unifyApplied` pipeline (same dedupe-by-posting-link, same source tags
 * manual/gmail/both, manual status authoritative, same no-fanout guard — a
 * rejection email links only to its #162-matched posting, never across a
 * company), then keeps only the entries whose resolved stage is `rejected`.
 *
 * `manualPostings` is `GET /postings?state=rejected` — postings the operator
 * manually marked rejected (resolved_status='rejected' is manual-only; the
 * company-level Gmail rejection is an informational hint, never folded in).
 */
export function UnifiedRejectedList({
  manualPostings,
  sort,
}: {
  manualPostings: readonly PostingListItem[];
  sort: AppliedSort;
}) {
  const { data: outcomes } = useAllOutcomes(true);

  const entries = useMemo(
    () =>
      sortUnified(unifyApplied(outcomes?.items ?? [], manualPostings), sort).filter(
        (e) => entryStage(e) === 'rejected',
      ),
    [outcomes, manualPostings, sort],
  );

  if (entries.length === 0) {
    return (
      <section
        data-testid="rejected-empty"
        className="flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
      >
        <h2 className="text-sm font-semibold">No rejections yet.</h2>
        <p className="text-[13px] text-muted-foreground">
          Rejections detected from Gmail and ones you mark Rejected in Triage show up here.
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
