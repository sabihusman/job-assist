'use client';

import { ChevronRight } from 'lucide-react';
import { useState } from 'react';

import { StageBadge } from '@/components/shared/StageBadge';
import { usePostingOutcomes } from '@/lib/api/applied';
import { type PipelineStage, STAGE_LABELS, stageOf } from '@/lib/applied/stages';
import type { PostingListItem } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * One row in the Applied list. Collapsed by default; clicking the row
 * toggles inline expansion that reveals a vertical TIMELINE of outcome
 * events. Outcomes are fetched lazily — the query is enabled only once
 * the row opens, so the initial page render doesn't fan out N outcome
 * requests.
 */
export function AppliedRow({
  posting,
  currentStage,
}: {
  posting: PostingListItem;
  currentStage: PipelineStage;
}) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = usePostingOutcomes(open ? posting.id : null);

  const tier = posting.company.tier ?? 4;
  const appliedAt = posting.state.current_at ?? posting.first_seen_at;

  return (
    <li className="rounded-md border border-border bg-card shadow-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={cn('flex w-full items-center gap-3 px-4 py-3 text-left', 'hover:bg-accent/30')}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            'h-4 w-4 shrink-0 text-muted-foreground transition-transform',
            open && 'rotate-90',
          )}
        />
        <TierBadge tier={tier} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex flex-wrap items-center gap-2 text-[14px] font-semibold">
            <span className="truncate">{posting.company.name}</span>
            <span aria-hidden="true" className="text-muted-foreground">
              ·
            </span>
            <span className="truncate text-foreground/90">{posting.role.title}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
            <span>applied {fmtMonthDay(appliedAt)}</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono text-[11px]">{fmtRelative(appliedAt)}</span>
            {posting.salary && (
              <>
                <span aria-hidden="true">·</span>
                <span className="font-mono text-[11px]">{fmtSalary(posting.salary)}</span>
              </>
            )}
            <span aria-hidden="true">·</span>
            <span className="font-mono text-[10px] uppercase tracking-wide">
              {posting.source.ats}
            </span>
          </div>
        </div>
        <StageBadge stage={currentStage} />
      </button>

      {open && (
        <div className="border-t border-border bg-surface-2/50 px-4 py-3">
          <h4 className="mb-3 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
            Timeline
          </h4>
          {isLoading ? (
            <div className="text-[12px] text-muted-foreground">Loading…</div>
          ) : (
            <Timeline appliedAt={appliedAt} events={data?.items ?? []} />
          )}
        </div>
      )}
    </li>
  );
}

function Timeline({
  appliedAt,
  events,
}: {
  appliedAt: string;
  events: { id: string; received_at: string; stage: string }[];
}) {
  // Always include the initial "Applied" event derived from the
  // posting_action timestamp — even when no Gmail-side outcome events
  // exist yet. The remote outcomes get bucketed via `stageOf`.
  const rows = [
    {
      id: 'initial-applied',
      receivedAt: appliedAt,
      stage: 'applied' as PipelineStage,
    },
    ...events
      .map((e) => ({
        id: e.id,
        receivedAt: e.received_at,
        stage: stageOf(e.stage),
      }))
      .filter(
        (r): r is { id: string; receivedAt: string; stage: PipelineStage } => r.stage !== null,
      )
      .sort((a, b) => Date.parse(a.receivedAt) - Date.parse(b.receivedAt)),
  ];

  return (
    <ol className="relative flex flex-col gap-3 pl-4">
      <span aria-hidden="true" className="absolute left-[5px] top-1 bottom-1 w-px bg-border" />
      {rows.map((row) => (
        <li key={row.id} className="relative flex items-center gap-3 text-[13px]">
          <span
            aria-hidden="true"
            className="absolute left-[-7px] top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-primary"
          />
          <StageBadge stage={row.stage} />
          <span className="text-muted-foreground">
            {STAGE_LABELS[row.stage]} · {fmtMonthDay(row.receivedAt)} ·{' '}
            {fmtRelative(row.receivedAt)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function TierBadge({ tier }: { tier: number }) {
  const tierClass =
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
        'shrink-0 rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
        tierClass,
      )}
    >
      T{tier}
    </span>
  );
}

function fmtMonthDay(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function fmtRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - then);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function fmtSalary(salary: NonNullable<PostingListItem['salary']>): string {
  const min = salary.min ? `$${Math.round(salary.min / 1000)}k` : null;
  const max = salary.max ? `$${Math.round(salary.max / 1000)}k` : null;
  if (min && max) return `${min}–${max}`;
  return min ?? max ?? '';
}
