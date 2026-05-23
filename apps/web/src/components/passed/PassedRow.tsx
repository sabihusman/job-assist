'use client';

import { REASON_CHOICES } from '@/components/triage/ReasonPicker';
import type { ActionReason, PostingListItem } from '@/lib/triage/types';
import { cn } from '@/lib/utils';

/**
 * One row in the /passed list (PR #50).
 *
 * Flat card. No expand/collapse, no timeline. Surfaces:
 *   - company name + role title
 *   - tier badge
 *   - date passed (relative + month/day)
 *   - inline reason chip ("Wrong role", "Too senior", …) from the
 *     existing REASON_CHOICES label set.
 *
 * Tiny formatting helpers are inlined here rather than extracted from
 * AppliedRow per the PR #50 strip rules ("copy the minimal pattern —
 * don't refactor existing pages"). Promote to a shared helper if a
 * third copy lands.
 */

const REASON_LABEL: Record<ActionReason, string> = REASON_CHOICES.reduce(
  (acc, c) => {
    acc[c.reason] = c.label;
    return acc;
  },
  {} as Record<ActionReason, string>,
);

export function PassedRow({ posting }: { posting: PostingListItem }) {
  const tier = posting.company.tier ?? 4;
  const passedAt = posting.state.current_at ?? posting.first_seen_at;
  const reason = posting.state.reason;
  const reasonLabel = reason ? REASON_LABEL[reason] : null;

  return (
    <li
      data-testid="passed-row"
      className="flex items-center gap-3 rounded-md border border-border bg-card px-4 py-3 shadow-card"
    >
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
          <span>passed {fmtMonthDay(passedAt)}</span>
          <span aria-hidden="true">·</span>
          <span className="font-mono text-[11px]">{fmtRelative(passedAt)}</span>
        </div>
      </div>
      {reasonLabel && (
        <span
          aria-label={`Reason: ${reasonLabel}`}
          className="shrink-0 rounded bg-surface-2 px-2 py-0.5 text-[11px] text-muted-foreground ring-1 ring-inset ring-border"
        >
          {reasonLabel}
        </span>
      )}
    </li>
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
