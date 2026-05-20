'use client';

import { useMemo } from 'react';

import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';
import { countAppliedByCompany, summarizeOutcomes } from '@/lib/companies/summaries';
import type { CompanyListItem } from '@/lib/companies/types';
import { cn } from '@/lib/utils';

/**
 * Companies table — read-only in #32c.
 *
 * v1 strips (per PR spec):
 *   - NOTES column hidden
 *   - "+ Add company" banner button not rendered
 *   - close/reopen action stripped (no backend endpoints)
 *   - `closed` tag stripped (`/companies` doesn't return status)
 *
 * Derived columns:
 *   - APPLIED: counts of applied postings per company, fetched
 *     in parallel with /companies and reused across pages.
 *   - OUTCOMES: phrase like "2 screens, 1 onsite" computed from
 *     all outcome events filtered to this company's posting_ids.
 */
export function CompaniesTable({
  companies,
}: {
  companies: readonly CompanyListItem[];
}) {
  const { data: applied } = useAppliedPostings();
  const { data: outcomes } = useAllOutcomes();

  const appliedCountByCompany = useMemo(
    () => countAppliedByCompany(applied?.items ?? []),
    [applied],
  );

  return (
    <table className="w-full border-separate border-spacing-0 text-[13px]">
      <thead>
        <tr className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          <Th>Name</Th>
          <Th>Tier</Th>
          <Th>ATS</Th>
          <Th align="right">Open</Th>
          <Th align="right">Applied</Th>
          <Th>Outcomes</Th>
        </tr>
      </thead>
      <tbody>
        {companies.map((c) => (
          <tr key={c.id} className="border-t border-border">
            <Td>{c.name}</Td>
            <Td>
              <TierBadge tier={c.tier} />
            </Td>
            <Td>
              {c.ats_set.length === 0 ? (
                <span className="text-muted-foreground">—</span>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {c.ats_set.map((ats) => (
                    <AtsBadge key={ats} ats={ats} />
                  ))}
                </div>
              )}
            </Td>
            <Td align="right" mono>
              {c.active_postings}
            </Td>
            <Td align="right" mono>
              {appliedCountByCompany.get(c.id) ?? 0}
            </Td>
            <Td>
              <span className="text-muted-foreground">
                {summarizeOutcomes(c.id, applied?.items ?? [], outcomes?.items ?? [])}
              </span>
            </Td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Th({
  children,
  align = 'left',
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <th
      scope="col"
      className={cn(
        'border-b border-border px-3 py-2 font-mono text-[11px] uppercase tracking-wide',
        align === 'right' ? 'text-right' : 'text-left',
      )}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = 'left',
  mono = false,
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
  mono?: boolean;
}) {
  return (
    <td
      className={cn(
        'px-3 py-2',
        align === 'right' ? 'text-right' : 'text-left',
        mono && 'font-mono text-[12px]',
      )}
    >
      {children}
    </td>
  );
}

function TierBadge({ tier }: { tier: number | null }) {
  if (tier === null) return <span className="text-muted-foreground">—</span>;
  const cls =
    (
      {
        1: 'bg-tier-1/15 text-tier-1 ring-tier-1/30',
        2: 'bg-tier-2/15 text-tier-2 ring-tier-2/30',
        3: 'bg-tier-3/15 text-tier-3 ring-tier-3/30',
        4: 'bg-tier-4/15 text-tier-4 ring-tier-4/30',
      } as const
    )[tier as 1 | 2 | 3 | 4] ?? 'bg-tier-4/15 text-tier-4 ring-tier-4/30';
  return (
    <span
      aria-label={`Tier ${tier}`}
      className={cn(
        'inline-flex rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
        cls,
      )}
    >
      T{tier}
    </span>
  );
}

function AtsBadge({ ats }: { ats: string }) {
  // PR #33 adds "workday" to the ATS vocabulary. No brand color yet,
  // so workday falls through to the muted-foreground default — same
  // tone the spec asks for ("neutral slate/gray").
  const cls =
    (
      {
        greenhouse: 'text-ats-greenhouse',
        lever: 'text-ats-lever',
        ashby: 'text-ats-ashby',
        workday: 'text-muted-foreground',
      } as const
    )[ats.toLowerCase() as 'greenhouse' | 'lever' | 'ashby' | 'workday'] ?? 'text-muted-foreground';
  return <span className={cn('font-mono text-[10px] uppercase tracking-wide', cls)}>{ats}</span>;
}
