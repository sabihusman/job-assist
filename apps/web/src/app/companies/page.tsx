'use client';

import { AppShell } from '@/components/chrome/AppShell';
import { CompaniesTable } from '@/components/companies/CompaniesTable';
import { ExportCsvButton } from '@/components/shared/ExportCsvButton';
import { useCompanies } from '@/lib/api/companies';
import { buildCompaniesCsv } from '@/lib/companies/exportCsv';

/**
 * Companies page (PR #32c). Read-only target-company table.
 *
 * Subtitle counts `{N} target companies` — the `/companies` response
 * doesn't include closed status, so the closed-count clause from
 * UI_SPEC.md is dropped until a future PR adds the field.
 */
export default function CompaniesPage() {
  const { data, isLoading, isError, error, refetch } = useCompanies();
  const items = data?.items ?? [];

  return (
    <AppShell
      title="Companies"
      subtitle={
        data
          ? `${items.length} target ${items.length === 1 ? 'company' : 'companies'}`
          : 'Target list'
      }
    >
      <div className="px-6 py-4">
        {isError ? (
          <ErrorCard message={(error as Error)?.message ?? 'Unknown error'} onRetry={refetch} />
        ) : isLoading ? (
          <Skeleton />
        ) : items.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="flex flex-col gap-3">
            {/* feat/view-exports: the table renders the full list from one
                fetch, so the export is a pure serialization of these rows. */}
            <div className="flex justify-end">
              <ExportCsvButton
                buildCsv={() => buildCompaniesCsv(items)}
                filenamePrefix="companies-export"
                testId="companies-export-button"
                title="Download a .csv of every company currently listed — same rows, same order."
              />
            </div>
            <div className="overflow-x-auto rounded-md border border-border bg-card">
              <CompaniesTable companies={items} />
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function Skeleton() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="h-10 animate-pulse rounded border border-border bg-surface-2" />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <section
      data-testid="companies-empty"
      className="mx-auto flex max-w-lg flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
    >
      <h2 className="text-sm font-semibold">No target companies configured.</h2>
      <p className="text-[13px] text-muted-foreground">
        Add via SQL or the upcoming admin endpoint.
      </p>
    </section>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="rounded-md border border-negative/40 bg-negative/5 p-4">
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load companies.</h2>
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
