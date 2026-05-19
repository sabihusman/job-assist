'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

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
  const { data, isLoading, isError, error, refetch } = useAppliedPostings();
  const items = data?.items ?? [];

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">
          {data ? `${items.length} active application${items.length === 1 ? '' : 's'}` : '…'}
        </p>
        <AppliedSortStrip />
      </div>

      {isError ? (
        <ErrorCard
          message={(error as Error)?.message ?? 'Unknown error'}
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <LoadingSkeleton />
      ) : (
        <AppliedList postings={items} sort={sort} />
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
    <section className="rounded-md border border-negative/40 bg-negative/5 p-4">
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
