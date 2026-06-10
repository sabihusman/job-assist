import { RepeatSignalBadges } from '@/components/shared/RepeatSignalBadges';
import type { RepeatSignals } from '@/lib/api/companySignals';
import type { ApplicationCard } from '@/lib/pipeline/bucket';
import { familyLabel } from '@/lib/triage/family-labels';
import { cn } from '@/lib/utils';

/**
 * Compact kanban card. Clickable (feat/pipeline-detail) — selecting it opens
 * the PipelineDetailPanel. Smaller padding and typography than TriageCard.
 */
export function PipelineCard({
  card,
  onSelect,
  signals,
}: {
  card: ApplicationCard;
  onSelect?: () => void;
  signals?: RepeatSignals;
}) {
  const tier = card.tier;
  const interactive = onSelect != null;
  return (
    <li
      data-card-id={card.id}
      {...(interactive && {
        role: 'button',
        tabIndex: 0,
        onClick: onSelect,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onSelect();
          }
        },
      })}
      className={cn(
        'flex flex-col gap-1 rounded-md border border-border bg-card p-2.5 shadow-card',
        interactive &&
          'cursor-pointer hover:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      )}
    >
      {typeof tier === 'number' && <TierBadge tier={tier} />}
      <span className="truncate text-[13px] font-semibold">{card.companyName}</span>
      <RepeatSignalBadges companyId={card.companyId} signals={signals} />
      <span className="truncate text-[12px] text-muted-foreground">
        {card.roleTitle}
        {card.roleFamily && `, ${familyLabel(card.roleFamily)}`}
      </span>
      <span className="font-mono text-[11px] text-muted-foreground">
        {fmtMonthDay(card.appliedAt)}
      </span>
    </li>
  );
}

function TierBadge({ tier }: { tier: number }) {
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
        'inline-flex w-fit rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
        cls,
      )}
    >
      T{tier}
    </span>
  );
}

function fmtMonthDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
