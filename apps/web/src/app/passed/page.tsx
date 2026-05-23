'use client';

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
 */
export default function PassedPage() {
  return (
    <AppShell title="Passed" subtitle="Roles you decided against">
      <PassedPageInner />
    </AppShell>
  );
}

function PassedPageInner() {
  const { data, isLoading, isError, error, refetch } = usePassedPostings();
  const items = data?.items ?? [];

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
      <p className="text-[13px] text-muted-foreground">
        {data ? `${items.length} passed posting${items.length === 1 ? '' : 's'}` : '…'}
      </p>

      {isError ? (
        <ErrorCard
          message={(error as Error)?.message ?? 'Unknown error'}
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <LoadingSkeleton />
      ) : items.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="flex list-none flex-col gap-3 p-0">
          {items.map((p) => (
            <PassedRow key={p.id} posting={p} />
          ))}
        </ul>
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
    <section className="rounded-md border border-negative/40 bg-negative/5 p-4">
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
