'use client';

import { AppShell } from '@/components/chrome/AppShell';
import { PassedRow } from '@/components/passed/PassedRow';
import { API_BASE_URL } from '@/lib/api/client';
import { usePassedPostingsInfinite } from '@/lib/api/state-views';

/**
 * /passed (PR #50).
 *
 * Operator-vocabulary name; the wire value is ``state=not_interested``.
 * Flat list of every posting whose latest posting_action is
 * not_interested, newest first (the endpoint's default sort).
 * No filter chips, no sort dropdown — strip per the PR brief.
 *
 * Pagination (PR #66 / Bestiary 5.11, migrated fix/audit #5): backed by
 * ``usePassedPostingsInfinite`` (a ``useInfiniteQuery`` accumulator, same
 * pattern as the main Triage list) so a second Load More click appends
 * rather than replacing the first extra page. No URL persistence —
 * refresh resets to page 1 (decision (a) per the PR brief).
 */
export default function PassedPage() {
  return (
    <AppShell title="Passed" subtitle="Roles you decided against">
      <PassedPageInner />
    </AppShell>
  );
}

function PassedPageInner() {
  const query = usePassedPostingsInfinite();

  const items = query.items;
  const total = query.total;
  const hasMore = query.hasNextPage ?? total > items.length;

  // Any non-2xx surfaces a deliberate error card. Bestiary 5.11.
  const isError = query.isError;
  const errorMsg = (query.error as Error)?.message ?? 'Unknown error';

  return (
    <div className="flex min-w-0 flex-col gap-4 px-4 py-4 md:px-6">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">
          {query.data ? `${items.length} of ${total} passed` : '…'}
        </p>
        {/* feat/view-exports: server-side export, Triage-style. The list is
            server-paginated, so the endpoint (same shared query builder as
            GET /postings) serializes the FULL not_interested set in the same
            default sort — every row this view paginates over, no cap. Plain
            <a href> so the browser's native download handles the
            Content-Disposition response. */}
        <a
          href={`${API_BASE_URL}/postings/export.xlsx?state=not_interested`}
          download
          data-testid="passed-export-link"
          className="inline-flex shrink-0 items-center gap-1.5 rounded border border-border bg-surface px-2.5 py-1 text-sm text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground"
          title="Download an .xlsx of every passed role — same sort, no row cap."
        >
          Export current view
        </a>
      </div>

      {isError ? (
        <ErrorCard message={errorMsg} onRetry={() => query.refetch()} />
      ) : query.isLoading ? (
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
              onClick={() => query.fetchNextPage()}
              disabled={query.isFetchingNextPage}
              className="self-center rounded-md border border-border bg-surface px-3 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
            >
              {query.isFetchingNextPage
                ? 'Loading…'
                : `Load more (${total - items.length} remaining)`}
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
