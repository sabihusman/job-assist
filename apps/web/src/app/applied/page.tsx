'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense, useMemo } from 'react';

import { AppliedSortStrip } from '@/components/applied/AppliedSortStrip';
import { UnifiedAppliedList } from '@/components/applied/UnifiedAppliedList';
import { AppShell } from '@/components/chrome/AppShell';
import { ExportCsvButton } from '@/components/shared/ExportCsvButton';
import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';
import { buildUnifiedCsv } from '@/lib/applied/exportCsv';
import type { AppliedSort } from '@/lib/applied/types';
import { sortUnified, unifyApplied } from '@/lib/applied/unify';

/**
 * Applied page (PR #32c).
 *
 * Full-width list. URL holds the sort param so the chosen view is
 * shareable. Inner component reads `useSearchParams` so it sits inside
 * a Suspense boundary; the fallback is plain markup that can render
 * statically.
 *
 * Pagination (PR #66 / Bestiary 5.11): page 1 (100 rows) renders
 * unconditionally; a second hook instance fires when the operator
 * clicks Load More. Mirrors ``OutreachTimeline.tsx``. No URL
 * persistence — refresh resets to page 1.
 *
 * Known limitation (carried over from the OutreachTimeline pattern):
 * clicking Load More twice replaces the previous "extra" page rather
 * than accumulating, so currently only 2 pages worth of rows render
 * (page 1 + one extra = 200). At current data volumes (~tens of
 * applications) this never surfaces. If volume crosses ~200 rows,
 * migrate this + OutreachTimeline to ``useInfiniteQuery`` in a
 * focused follow-up.
 */
export default function AppliedPage() {
  return (
    <AppShell title="Applied" subtitle="Sent and waiting">
      <Suspense fallback={<PageFallback />}>
        <AppliedPageInner />
      </Suspense>
    </AppShell>
  );
}

function AppliedPageInner() {
  const searchParams = useSearchParams();
  const sort = (searchParams.get('sort') as AppliedSort | null) ?? 'applied';

  // feat/applied-unified: the manual Applied funnel is now an OVERLAY, not the
  // membership source. Manual is tiny (~4) so a single page suffices — Load
  // More is gone. The Pipeline (Gmail, ~150) is the authoritative membership
  // source and is fetched inside UnifiedAppliedList (shared cache with the
  // Pipeline page). We fetch it here too only to show the resolved count; React
  // Query dedupes the request by key.
  const manual = useAppliedPostings();
  const outcomes = useAllOutcomes(true);

  const manualPostings = manual.data?.items ?? [];
  // feat/view-exports: keep the SORTED entries (not just the count) so the
  // export serializes exactly the list the operator sees — same unify, same
  // sort. UnifiedAppliedList recomputes from the same cached queries; React
  // Query dedupes the fetches and unify is a cheap pure fold.
  const entries = useMemo(
    () => sortUnified(unifyApplied(outcomes.data?.items ?? [], manualPostings), sort),
    [outcomes.data, manualPostings, sort],
  );
  const count = entries.length;

  const isError = manual.isError || outcomes.isError;
  const errorMsg =
    (manual.error as Error)?.message ?? (outcomes.error as Error)?.message ?? 'Unknown error';
  const isLoading = manual.isLoading || outcomes.isLoading;

  return (
    <div className="flex min-w-0 flex-col gap-4 px-4 py-4 md:px-6">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">
          {isLoading ? '…' : `${count} application${count === 1 ? '' : 's'}`}
        </p>
        <div className="flex items-center gap-2">
          <ExportCsvButton
            buildCsv={() => buildUnifiedCsv(entries)}
            filenamePrefix="applied-export"
            disabled={isLoading || count === 0}
            testId="applied-export-button"
            title="Download a .csv of every application currently listed — same merge, same sort."
          />
          <AppliedSortStrip />
        </div>
      </div>

      {isError ? (
        <ErrorCard
          message={errorMsg}
          onRetry={() => {
            manual.refetch();
            outcomes.refetch();
          }}
        />
      ) : isLoading ? (
        <LoadingSkeleton />
      ) : (
        <UnifiedAppliedList manualPostings={manualPostings} sort={sort} />
      )}
    </div>
  );
}

function PageFallback() {
  return (
    <div className="flex min-w-0 flex-col gap-4 px-4 py-4 md:px-6">
      <div className="h-6 w-64 animate-pulse rounded bg-surface-2" />
      <LoadingSkeleton />
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-[68px] animate-pulse rounded-md border border-border bg-surface-2"
        />
      ))}
    </div>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section
      data-testid="applied-error"
      className="rounded-md border border-negative/40 bg-negative/5 p-4"
    >
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load applications.</h2>
      <p className="mt-1 text-[13px] text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
      >
        Retry
      </button>
    </section>
  );
}
