'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback } from 'react';

import { ExportButton } from '@/components/triage/ExportButton';
import { SortDropdown } from '@/components/triage/SortDropdown';
import { FAMILY_CHIPS, FAMILY_LABELS } from '@/lib/triage/family-labels';
import { encodeFilters, parseFilters, toggleInArray } from '@/lib/triage/filters';
import type { Ats, RemoteType, RoleFamilyWire, SortKey, TriageFilters } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * Multi-select filter chips. Lives in URL search params (not zustand)
 * so views are shareable as links — see `lib/triage/filters.ts` for the
 * codec.
 *
 * Each chip group's onChange computes the next `URLSearchParams`,
 * preserves untouched params (e.g. `state`), and calls
 * `router.replace(...)` so the back-button only stores the user's
 * navigations, not every chip click. Replace also avoids a render
 * cascade — the next render reads the new search params on its own.
 */

const TIER_CHIPS = [
  { wire: 1, label: 'T1' },
  { wire: 2, label: 'T2' },
  { wire: 3, label: 'T3' },
  { wire: 4, label: 'T4' },
] as const;
const ATS_CHIPS = [
  { wire: 'greenhouse' as Ats, label: 'greenhouse' },
  { wire: 'lever' as Ats, label: 'lever' },
  { wire: 'ashby' as Ats, label: 'ashby' },
  // PR #33 added the Workday adapter; PR #43 exposes the chip.
  { wire: 'workday' as Ats, label: 'workday' },
  // PR #55: iCIMS adapter chip. Same row, no badge color — falls through
  // to the muted-foreground default in TriageCard/CompaniesTable.
  { wire: 'icims' as Ats, label: 'icims' },
] as const;
const REMOTE_CHIPS = [
  { wire: 'remote' as RemoteType, label: 'remote' },
  { wire: 'hybrid' as RemoteType, label: 'hybrid' },
  { wire: 'onsite' as RemoteType, label: 'onsite' },
] as const;

export function FilterRow({
  showing,
  total,
}: {
  showing: number;
  total: number;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const filters = parseFilters(searchParams);

  // `pushFilters` writes a complete TriageFilters back to URL.
  const pushFilters = useCallback(
    (next: TriageFilters) => {
      const params = encodeFilters(next);
      const search = params.toString();
      router.replace(search ? `/?${search}` : '/', { scroll: false });
    },
    [router],
  );

  return (
    <div className="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center md:gap-x-6 md:gap-y-3">
      {/* PR 2 UX overhaul: chip-groups bar.
          - At < md: horizontal scroll, single row. Four chip groups at
            vertical stacking would take 8+ rows of vertical real estate
            before the list starts — annoying on mobile. Horizontal swipe
            keeps the filter row to one row of height.
          - At md+: contents fall out of this wrapper and the outer
            container's ``md:flex-wrap`` takes over — the same multi-line
            wrap behavior as before this PR. */}
      <div className="-mx-2 flex snap-x snap-mandatory items-start gap-x-6 overflow-x-auto px-2 pb-1 md:m-0 md:flex-wrap md:overflow-visible md:p-0 md:pb-0">
        <ChipGroupRow
          label="TIER"
          current={filters.tier}
          chips={TIER_CHIPS.map((c) => ({ wire: c.wire, label: c.label }))}
          onToggle={(v) => pushFilters({ ...filters, tier: toggleInArray(filters.tier, v) })}
        />
        <ChipGroupRow
          label="SOURCE"
          current={filters.ats}
          chips={ATS_CHIPS.map((c) => ({ wire: c.wire, label: c.label }))}
          onToggle={(v) => pushFilters({ ...filters, ats: toggleInArray(filters.ats, v) })}
        />
        <ChipGroupRow
          label="REMOTE"
          current={filters.remote_type}
          chips={REMOTE_CHIPS.map((c) => ({ wire: c.wire, label: c.label }))}
          onToggle={(v) =>
            pushFilters({ ...filters, remote_type: toggleInArray(filters.remote_type, v) })
          }
        />
        <ChipGroupRow
          label="FAMILY"
          current={filters.role_family}
          chips={FAMILY_CHIPS.map((wire) => ({ wire, label: FAMILY_LABELS[wire] }))}
          onToggle={(v) =>
            pushFilters({
              ...filters,
              role_family: toggleInArray(filters.role_family, v as RoleFamilyWire),
            })
          }
        />
        {/* feat/pm-po-only-filter: default-on PM/PO gate. Reversible (NOT a
            hard exclude) because the role_family classifier is unreliable —
            the operator toggles OFF to audit what it's hiding. Explicit
            FAMILY chips override it. */}
        <div className="flex shrink-0 snap-start items-center gap-2 md:flex-wrap">
          <span className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
            ROLE
          </span>
          <button
            type="button"
            onClick={() => pushFilters({ ...filters, pm_only: !filters.pm_only })}
            data-selected={filters.pm_only}
            aria-pressed={filters.pm_only}
            title="Show only Product Management + Product Owner roles. Toggle off to see every family."
            className={cn(
              'shrink-0 rounded px-2 py-0.5 text-sm ring-1 ring-inset transition-colors',
              filters.pm_only
                ? 'bg-accent text-foreground ring-border-strong'
                : 'bg-surface text-muted-foreground ring-border hover:text-foreground',
            )}
          >
            PM/PO only
          </button>
        </div>
      </div>

      {/* PR #49: SortDropdown is right-aligned alongside the count label.
          PR 2: at <md it stays full-width below the chip row. At md+
          the parent's ``md:flex-row + md:flex-wrap`` lets ``ml-auto``
          push it right as before. */}
      <div className="flex items-center justify-between gap-4 md:ml-auto md:justify-end">
        <SortDropdown
          value={filters.sort}
          onChange={(next: SortKey) => pushFilters({ ...filters, sort: next })}
        />
        {/* feat/triage-export-xlsx: anchor download — see ExportButton.tsx
            for why this is a plain `<a href>` instead of fetch + blob. */}
        <ExportButton />
        <div className="text-sm text-muted-foreground">
          showing {showing} of {total}
        </div>
      </div>
    </div>
  );
}

function ChipGroupRow<T extends string | number>({
  label,
  current,
  chips,
  onToggle,
}: {
  label: string;
  current: readonly T[];
  chips: readonly { wire: T; label: string }[];
  onToggle: (value: T) => void;
}) {
  return (
    // PR 2: ``shrink-0 snap-start`` keeps each chip group as one
    // snap-target in the < md horizontal-scroll container. Removed
    // ``flex-wrap`` from the chip wrapper inside — at < md the parent
    // is single-row scrollable; at md+ the outer wrap handles overflow.
    <div className="flex shrink-0 snap-start items-center gap-2 md:flex-wrap">
      <span className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="flex items-center gap-1.5 md:flex-wrap">
        {chips.map((c) => {
          const selected = current.includes(c.wire);
          return (
            <button
              key={String(c.wire)}
              type="button"
              onClick={() => onToggle(c.wire)}
              data-selected={selected}
              aria-pressed={selected}
              className={cn(
                'shrink-0 rounded px-2 py-0.5 text-sm ring-1 ring-inset transition-colors',
                selected
                  ? 'bg-accent text-foreground ring-border-strong'
                  : 'bg-surface text-muted-foreground ring-border hover:text-foreground',
              )}
            >
              {c.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
