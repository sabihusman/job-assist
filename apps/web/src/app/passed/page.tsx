'use client';

import { useState } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { PassedRow } from '@/components/passed/PassedRow';
import { usePassedPostings } from '@/lib/api/state-views';

/**
 * /passed (PR #50).
 *
 * Operator-vocabulary name; the wire value is ``state=not_interested``.
 * Flat list of every posting whose latest posting_action is
 * not_interested, newest first (the endpoint's default sort).
 * No filter chips, no sort dropdown — strip per the PR brief.
 *
 * Pagination (PR #66 / Bestiary 5.11): page 1 (100 rows) renders
 * unconditionally; a second hook instance fires only when the operator
 * clicks Load More, and its items concatenate onto page 1. Mirrors
 * ``OutreachTimeline.tsx``. No URL persistence — refresh resets to
 * page 1 (decision (a) per the PR brief).
 */
export default function PassedPage() {
  return (
    <AppShell title="Passed" subtitle="Roles you decided against">
      <PassedPageInner />
    </AppShell>
  );
}

function PassedPageInner() {
  const page1 = usePassedPostings();

  // Operator-driven extra pages. ``extraOffset`` is the offset of the
  // most recently requested additional page. Clicking Load More
  // advances it to the current ``items.length``. The query is gated
  // by ``enabled`` so it only fires after the first click.
  const [extraOffset, setExtraOffset] = useState<number | null>(null);
  const extra = usePassedPostings(extraOffset ?? 0, extraOffset !== null);

  const page1Items = page1.data?.items ?? [];
  const items =
    extraOffset !== null && extra.data ? [...page1Items, ...extra.data.items] : page1Items;
  const total = page1.data?.total ?? 0;
  const hasMore = total > items.length;

  // Any non-2xx surfaces a deliberate error card. Bestiary 5.11.
  const isError = page1.isError || extra.isError;
  const errorMsg =
    (page1.error as Error)?.message ?? (extra.error as Error)?.message ?? 'Unknown error';

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
      <p className="text-[13px] text-muted-foreground">
        {page1.data ? `${items.length} of ${total} passed` : '…'}
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
              <PassedRow key={p.id} posting={p} />
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
      data-testid="passed-empty"
      className="flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
    >
      <h2 className="text-sm font-semibold">No passed postings yet.</h2>
      <p className="text-[13px] text-muted-foreground">
        Postings you press <kbd>2</kbd> on in Triage land here.
      </p>
    </section>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section
      data-testid="passed-error"
      className="rounded-md border border-negative/40 bg-negative/5 p-4"
    >
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load passed postings.</h2>
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
