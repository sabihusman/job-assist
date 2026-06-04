'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useMemo, useState } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { ContactDetailPanel } from '@/components/contacts/ContactDetailPanel';
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
 * PII discipline (PR #72 split):
 *   - ``source_type`` lives in the URL. It's an enum
 *     (``tippie_alumni`` | ``linkedin_outreach`` | ...), not PII —
 *     putting it in the URL makes filter state shareable, refresh-
 *     stable, and consistent with how Triage handles its filters.
 *   - ``search`` and ``include_archived`` stay in component state.
 *     The search field can contain typed names — that's sensitive
 *     enough that it shouldn't end up in browser history bars.
 *   - ``useSearchParams`` requires a Suspense boundary in the App
 *     Router (same pattern as ``/applied``, ``/page.tsx``).
 */
const SOURCE_OPTIONS: ContactSourceType[] = [
  'tippie_alumni',
  'linkedin_outreach',
  'recruiter_inbound',
  'warm_intro',
];

const VALID_SOURCES = new Set<ContactSourceType>(SOURCE_OPTIONS);

export default function ContactsPage() {
  return (
    <AppShell title="Contacts" subtitle="Outreach pipeline">
      <Suspense fallback={<LoadingSkeleton />}>
        <ContactsPageInner />
      </Suspense>
    </AppShell>
  );
}

function ContactsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Source filter derives from the URL on every render — single source
  // of truth. Filter-set values not in the enum are dropped (stale
  // links / URL tampering shouldn't crash the page).
  const sourceFromUrl: ContactSourceType[] = useMemo(() => {
    return searchParams
      .getAll('source_type')
      .filter((s): s is ContactSourceType => VALID_SOURCES.has(s as ContactSourceType));
  }, [searchParams]);

  // The non-PII fields that DON'T go to the URL stay in component
  // state. Default include_archived=false, default search="".
  const [stateBits, setStateBits] = useState({
    search: DEFAULT_CONTACTS_FILTERS.search,
    include_archived: DEFAULT_CONTACTS_FILTERS.include_archived,
  });

  const filters = useMemo(
    () => ({
      ...DEFAULT_CONTACTS_FILTERS,
      ...stateBits,
      source_type: sourceFromUrl,
    }),
    [stateBits, sourceFromUrl],
  );

  const [selectedContactId, setSelectedContactId] = useState<string | null>(null);
  const {
    items,
    total,
    isLoading,
    isError,
    error,
    refetch,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useContacts(filters);

  const toggleSource = (source: ContactSourceType) => {
    // Mutate the URL (replace, not push — filter toggles shouldn't
    // pollute browser history). Round-trip via URLSearchParams so we
    // preserve any non-source params someone might have appended.
    const next = new URLSearchParams(searchParams.toString());
    const current = next.getAll('source_type');
    const has = current.includes(source);
    next.delete('source_type');
    const after = has ? current.filter((s) => s !== source) : [...current, source];
    for (const s of after) next.append('source_type', s);
    const qs = next.toString();
    router.replace(qs ? `/contacts?${qs}` : '/contacts', { scroll: false });
  };

  return (
    <div className="flex min-w-0 flex-col gap-4 px-6 py-4">
      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <FilterGroup label="SOURCE">
          {SOURCE_OPTIONS.map((src) => {
            const selected = sourceFromUrl.includes(src);
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
            value={stateBits.search}
            onChange={(e) => setStateBits((s) => ({ ...s, search: e.target.value }))}
            placeholder="name…"
            className="rounded bg-surface px-2 py-0.5 text-xs ring-1 ring-inset ring-border text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <input
            type="checkbox"
            checked={stateBits.include_archived}
            onChange={(e) => setStateBits((s) => ({ ...s, include_archived: e.target.checked }))}
          />
          Show archived
        </label>

        <div className="ml-auto text-[12px] text-muted-foreground">
          {isLoading ? '…' : `${items.length} of ${total}`}
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
        <>
          <ContactsTable
            contacts={items}
            showingArchived={stateBits.include_archived}
            onOpenDetail={setSelectedContactId}
            selectedId={selectedContactId}
          />
          {/* fix/contacts-pagination: Load More so all 374 alumni are
              reachable (was hard-capped at the first 50). Hidden once every
              row is loaded. */}
          {hasNextPage ? (
            <div className="flex justify-center pt-1">
              <button
                type="button"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
                className="inline-flex h-9 items-center rounded-md border border-border bg-surface px-4 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-60"
              >
                {isFetchingNextPage ? 'Loading…' : `Load more (${total - items.length} more)`}
              </button>
            </div>
          ) : null}
        </>
      )}

      <ContactDetailPanel
        contactId={selectedContactId}
        onClose={() => setSelectedContactId(null)}
      />
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
