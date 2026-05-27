'use client';

import Link from 'next/link';
import { useMemo } from 'react';

import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';
import { countAppliedByCompany, summarizeOutcomes } from '@/lib/companies/summaries';
import type { CompanyListItem } from '@/lib/companies/types';
import { cn } from '@/lib/utils';

/**
 * PR #71: a company is "soft-paused" when the operator has cleared the
 * ATS handle (so the ingest probe stops scraping it) but kept the row
 * in the target list with a ``notes`` field explaining why. Treat
 * ``ats === null`` or ``ats === 'unknown'`` plus a ``null`` handle as
 * "no adapter at all" (default state) rather than paused — pause is
 * specifically "we had a working adapter and intentionally stopped".
 */
function isPaused(c: CompanyListItem): boolean {
  return c.ats_handle === null && c.ats !== null && c.ats !== 'unknown';
}

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
          <tr key={c.id} className="border-t border-border hover:bg-accent/30">
            <Td>
              {/* PR #71: company name links to Triage filtered by this
                  company. ``target_company_id`` is already plumbed through
                  parseFilters → toQuery → backend ``/postings`` filter.
                  PR #75: notes render inline below the name (muted, 12px)
                  instead of in a ``title=`` tooltip — keyboard / mobile
                  users couldn't surface hover-only content. Mirrors the
                  ContactDetailPanel "source under name" pattern. */}
              <div className="flex flex-col">
                <Link
                  href={`/?target_company_id=${c.id}&state=triage`}
                  className="font-medium text-foreground hover:underline focus-visible:underline focus-visible:outline-none"
                >
                  {c.name}
                </Link>
                {c.notes && <p className="mt-0.5 text-[12px] text-muted-foreground">{c.notes}</p>}
              </div>
            </Td>
            <Td>
              <TierBadge tier={c.tier} />
            </Td>
            <Td>
              <div className="flex flex-wrap items-center gap-1">
                {c.ats_set.length === 0 ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  c.ats_set.map((ats) => <AtsBadge key={ats} ats={ats} />)
                )}
                {isPaused(c) && <PausedBadge title={c.notes ?? 'Paused'} />}
              </div>
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

function PausedBadge({ title }: { title: string }) {
  return (
    <span
      title={title}
      aria-label={`Paused — ${title}`}
      className="inline-flex rounded bg-pending/15 px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide text-pending ring-1 ring-inset ring-pending/30"
    >
      Paused
    </span>
  );
}

function AtsBadge({ ats }: { ats: string }) {
  // PR #33 added "workday"; PR #55 added "icims". Neither has a brand
  // color yet, so both fall through to the muted-foreground default —
  // same tone the spec asks for ("neutral slate/gray"). Explicit entries
  // here make the omissions intentional rather than accidental.
  const cls =
    (
      {
        greenhouse: 'text-ats-greenhouse',
        lever: 'text-ats-lever',
        ashby: 'text-ats-ashby',
        workday: 'text-muted-foreground',
        icims: 'text-muted-foreground',
      } as const
    )[ats.toLowerCase() as 'greenhouse' | 'lever' | 'ashby' | 'workday' | 'icims'] ??
    'text-muted-foreground';
  return <span className={cn('font-mono text-[10px] uppercase tracking-wide', cls)}>{ats}</span>;
}
