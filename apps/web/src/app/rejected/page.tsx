'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense, useMemo } from 'react';

import { AppliedSortStrip } from '@/components/applied/AppliedSortStrip';
import { AppShell } from '@/components/chrome/AppShell';
import { UnifiedRejectedList } from '@/components/rejected/UnifiedRejectedList';
import { useAllOutcomes } from '@/lib/api/applied';
import { useRejectedPostings } from '@/lib/api/state-views';
import type { AppliedSort } from '@/lib/applied/types';
import { entryStage, unifyApplied } from '@/lib/applied/unify';

/**
 * /rejected — unified rejection view (feat/rejected-unified).
 *
 * Mirrors the Applied tab (#163): the union of Gmail-detected rejections
 * (Pipeline REJECTED stage) and manually-rejected postings, deduped by
 * posting-link and source-tagged (manual / gmail / both), with the manual
 * status authoritative — built by the SAME `unifyApplied` pipeline, then
 * filtered to the rejected stage. The manual rejected funnel is the OVERLAY
 * (tiny); the Gmail set is the membership source (shared cache with Pipeline /
 * Applied, so navigating between them doesn't refetch).
 */
export default function RejectedPage() {
  return (
    <AppShell title="Rejected" subtitle="Closed by the company">
      <Suspense fallback={<PageFallback />}>
        <RejectedPageInner />
      </Suspense>
    </AppShell>
  );
}

function RejectedPageInner() {
  const searchParams = useSearchParams();
  const sort = (searchParams.get('sort') as AppliedSort | null) ?? 'applied';

  const manual = useRejectedPostings();
  const outcomes = useAllOutcomes(true);

  const manualPostings = manual.data?.items ?? [];
  const count = useMemo(
    () =>
      unifyApplied(outcomes.data?.items ?? [], manualPostings).filter(
        (e) => entryStage(e) === 'rejected',
      ).length,
    [outcomes.data, manualPostings],
  );

  const isError = manual.isError || outcomes.isError;
  const errorMsg =
    (manual.error as Error)?.message ?? (outcomes.error as Error)?.message ?? 'Unknown error';
  const isLoading = manual.isLoading || outcomes.isLoading;

  return (
    <div className="flex min-w-0 flex-col gap-4 px-4 py-4 md:px-6">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">
          {isLoading ? '…' : `${count} rejection${count === 1 ? '' : 's'}`}
        </p>
        <AppliedSortStrip />
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
        <UnifiedRejectedList manualPostings={manualPostings} sort={sort} />
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
      data-testid="rejected-error"
      className="rounded-md border border-negative/40 bg-negative/5 p-4"
    >
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load rejected postings.</h2>
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
