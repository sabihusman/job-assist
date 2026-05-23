'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback } from 'react';

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
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
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

      {/* PR #49: SortDropdown is right-aligned alongside the count label.
          Wraps to its own row on narrow viewports thanks to the parent's
          `flex-wrap`. Changing it preserves all other filters via the
          spread — same idiom as the chip groups above. */}
      <div className="ml-auto flex items-center gap-4">
        <SortDropdown
          value={filters.sort}
          onChange={(next: SortKey) => pushFilters({ ...filters, sort: next })}
        />
        <div className="text-[12px] text-muted-foreground">
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
    <div className="flex items-center gap-2">
      <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">
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
                'rounded px-2 py-0.5 text-xs ring-1 ring-inset transition-colors',
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
