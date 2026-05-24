'use client';

import { useState } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { ContactsTable } from '@/components/contacts/ContactsTable';
import { useContacts } from '@/lib/api/contacts';
import {
  CONTACT_SOURCE_LABELS,
  type ContactSourceType,
  DEFAULT_CONTACTS_FILTERS,
} from '@/lib/contacts/types';
import { cn } from '@/lib/utils';

/**
 * /contacts (PR #51).
 *
 * Read-only list of outreach contacts. Source filter chips, search,
 * archived toggle. No CRUD — that ships in PR #52.
 *
 * PII discipline: filter state lives in component state, NOT in the
 * URL. Names + emails are sensitive enough that they shouldn't end up
 * in browser history bars when the operator types a search query.
 */
export default function ContactsPage() {
  return (
    <AppShell title="Contacts" subtitle="Outreach pipeline">
      <ContactsPageInner />
    </AppShell>
  );
}

const SOURCE_OPTIONS: ContactSourceType[] = [
  'tippie_alumni',
  'linkedin_outreach',
  'recruiter_inbound',
  'warm_intro',
];

function ContactsPageInner() {
  const [filters, setFilters] = useState(DEFAULT_CONTACTS_FILTERS);
  const { data, isLoading, isError, error, refetch } = useContacts(filters);
  const items = data?.items ?? [];

  const toggleSource = (source: ContactSourceType) => {
    setFilters((f) => {
      const next = f.source_type.includes(source)
        ? f.source_type.filter((s) => s !== source)
        : [...f.source_type, source];
      return { ...f, source_type: next, offset: 0 };
    });
  };

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <FilterGroup label="SOURCE">
          {SOURCE_OPTIONS.map((src) => {
            const selected = filters.source_type.includes(src);
            return (
              <button
                key={src}
                type="button"
                onClick={() => toggleSource(src)}
                aria-pressed={selected}
                className={cn(
                  'rounded px-2 py-0.5 text-xs ring-1 ring-inset transition-colors',
                  selected
                    ? 'bg-accent text-foreground ring-border-strong'
                    : 'bg-surface text-muted-foreground ring-border hover:text-foreground',
                )}
              >
                {CONTACT_SOURCE_LABELS[src]}
              </button>
            );
          })}
        </FilterGroup>

        <div className="flex items-center gap-2">
          <label
            htmlFor="contacts-search"
            className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground"
          >
            SEARCH
          </label>
          <input
            id="contacts-search"
            type="search"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value, offset: 0 }))}
            placeholder="name…"
            className="rounded bg-surface px-2 py-0.5 text-xs ring-1 ring-inset ring-border text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <input
            type="checkbox"
            checked={filters.include_archived}
            onChange={(e) =>
              setFilters((f) => ({ ...f, include_archived: e.target.checked, offset: 0 }))
            }
          />
          Show archived
        </label>

        <div className="ml-auto text-[12px] text-muted-foreground">
          {data ? `${items.length} of ${data.total}` : '…'}
        </div>
      </div>

      {isError ? (
        <ErrorCard
          message={(error as Error)?.message ?? 'Unknown error'}
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <LoadingSkeleton />
      ) : (
        <ContactsTable contacts={items} showingArchived={filters.include_archived} />
      )}
    </div>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-10 animate-pulse rounded-md border border-border bg-surface-2" />
      ))}
    </div>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="rounded-md border border-negative/40 bg-negative/5 p-4">
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load contacts.</h2>
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
