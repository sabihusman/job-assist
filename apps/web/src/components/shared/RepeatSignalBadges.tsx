import type { RepeatSignals } from '@/lib/api/companySignals';
import { cn } from '@/lib/utils';

/**
 * Repeat-signal badges for a company (feat/repeat-signal-flags). Pure +
 * presentational: it takes the already-fetched ``signals`` map (one cached
 * `useCompanySignals` fetch lives at the page / detail-panel level and is passed
 * down), so leaf components like PipelineCard don't each couple to React Query.
 * Renders nothing when the company is unknown or below threshold.
 *
 *   • 2+ rejection outcomes  → "N rejections here"  (negative)
 *   • 2+ still-alive apps     → "N active apps here" (pending)
 */
export function RepeatSignalBadges({
  companyId,
  signals,
  className,
}: {
  companyId: string | null | undefined;
  signals: RepeatSignals | undefined;
  className?: string;
}) {
  if (!companyId || !signals) return null;
  const sig = signals[companyId];
  if (!sig) return null;

  const showRejections = sig.rejections >= 2;
  const showActive = sig.active_apps >= 2;
  if (!showRejections && !showActive) return null;

  return (
    <span
      data-testid="repeat-signal-badges"
      className={cn('inline-flex flex-wrap items-center gap-1', className)}
    >
      {showRejections && (
        <Badge
          tone="negative"
          label={`${sig.rejections} rejections here`}
          title={`You have ${sig.rejections} rejections from this company.`}
        />
      )}
      {showActive && (
        <Badge
          tone="pending"
          label={`${sig.active_apps} active apps here`}
          title={`You have ${sig.active_apps} still-alive applications at this company.`}
        />
      )}
    </span>
  );
}

function Badge({
  tone,
  label,
  title,
}: {
  tone: 'negative' | 'pending';
  label: string;
  title: string;
}) {
  const cls =
    tone === 'negative'
      ? 'bg-negative/15 text-negative ring-negative/30'
      : 'bg-pending/15 text-pending ring-pending/30';
  return (
    <span
      title={title}
      className={cn(
        'inline-flex w-fit items-center rounded px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset',
        cls,
      )}
    >
      {label}
    </span>
  );
}
