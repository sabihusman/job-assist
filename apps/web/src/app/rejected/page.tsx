'use client';

import { useState } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { RejectedRow } from '@/components/rejected/RejectedRow';
import { useRejectedPostings } from '@/lib/api/state-views';

/**
 * /rejected (PR #50).
 *
 * Postings where a Gmail-classified rejection email landed. Empty in v1
 * production until the rejection-detection cron starts producing rows
 * (PR #53 area). The page still ships — empty state is the dominant
 * user experience for now.
 *
 * Pagination (PR #66 / Bestiary 5.11): page 1 (100 rows) renders
 * unconditionally; Load More fires a second hook instance. Mirrors
 * ``OutreachTimeline.tsx``. Same 2-page limitation noted on
 * ``/applied`` applies — migrate to ``useInfiniteQuery`` if/when row
 * volume exceeds ~200.
 */
export default function RejectedPage() {
  return (
    <AppShell title="Rejected" subtitle="Closed by the company">
      <RejectedPageInner />
    </AppShell>
  );
}

function RejectedPageInner() {
  const page1 = useRejectedPostings();

  const [extraOffset, setExtraOffset] = useState<number | null>(null);
  const extra = useRejectedPostings(extraOffset ?? 0, extraOffset !== null);

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
      <p className="text-[13px] text-muted-foreground">
        {page1.data ? `${items.length} of ${total} rejected` : '…'}
      </p>

      {isError ? (
        <ErrorCard message={errorMsg} onRetry={() => page1.refetch()} />
      ) : page1.isLoading ? (
        <LoadingSkeleton />
      ) : items.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          <ul className="flex list-none flex-col gap-3 p-0">
            {items.map((p) => (
              <RejectedRow key={p.id} posting={p} />
            ))}
          </ul>
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

function EmptyState() {
  return (
    <section
      data-testid="rejected-empty"
      className="flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
    >
      <h2 className="text-sm font-semibold">No rejected postings yet.</h2>
      <p className="text-[13px] text-muted-foreground">
        Rejection emails detected from Gmail will surface here.
      </p>
    </section>
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
