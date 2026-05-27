'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';

import { AppliedList } from '@/components/applied/AppliedList';
import { AppliedSortStrip } from '@/components/applied/AppliedSortStrip';
import { AppShell } from '@/components/chrome/AppShell';
import { useAppliedPostings } from '@/lib/api/applied';
import type { AppliedSort } from '@/lib/applied/types';

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

  const page1 = useAppliedPostings();
  const [extraOffset, setExtraOffset] = useState<number | null>(null);
  const extra = useAppliedPostings(extraOffset ?? 0, extraOffset !== null);

  const page1Items = page1.data?.items ?? [];
  const items =
    extraOffset !== null && extra.data ? [...page1Items, ...extra.data.items] : page1Items;
  const total = page1.data?.total ?? 0;
  const hasMore = total > items.length;

  const isError = page1.isError || extra.isError;
  const errorMsg =
    (page1.error as Error)?.message ?? (extra.error as Error)?.message ?? 'Unknown error';

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">
          {page1.data ? `${items.length} of ${total} active` : '…'}
        </p>
        <AppliedSortStrip />
      </div>

      {isError ? (
        <ErrorCard message={errorMsg} onRetry={() => page1.refetch()} />
      ) : page1.isLoading ? (
        <LoadingSkeleton />
      ) : (
        <>
          <AppliedList postings={items} sort={sort} />
          {hasMore && (
            <button
              type="button"
              onClick={() => setExtraOffset(items.length)}
              disabled={extra.isLoading}
              className="self-center rounded-md border border-border bg-surface px-3 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
            >
              {extra.isLoading ? 'Loading…' : `Load more (${total - items.length} remaining)`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function PageFallback() {
  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
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
