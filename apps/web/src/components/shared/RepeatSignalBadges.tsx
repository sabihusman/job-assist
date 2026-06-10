import type { RepeatSignals } from '@/lib/api/companySignals';
import { normalizeCompanyName } from '@/lib/pipeline/normalizeCompanyName';
import { cn } from '@/lib/utils';

/**
 * Company-level application-awareness badges (feat/company-app-awareness). Pure +
 * presentational: it takes the already-fetched ``signals`` map (one cached
 * `useCompanySignals` fetch lives at the page / detail-panel level and is passed
 * down) so leaf components like TriageCard / PipelineCard don't each couple to
 * React Query. The map is keyed by NORMALIZED company name, so the badge
 * re-normalizes ``companyName`` to look it up. Renders nothing when the company
 * is unknown or has no signal.
 *
 * Thresholds (advisory only — never blocking):
 *   • active apps  1–2  → NEUTRAL "N active apps"   (informational)
 *   • active apps  ≥3   → AMBER   "N active apps"    (warning: portfolio heavy)
 *   • rejections   ≥1   → NEUTRAL "N rejections here"
 *   • 0 on an axis      → no badge for that axis
 */
export function RepeatSignalBadges({
  companyName,
  signals,
  className,
}: {
  companyName: string | null | undefined;
  signals: RepeatSignals | undefined;
  className?: string;
}) {
  if (!companyName || !signals) return null;
  const key = normalizeCompanyName(companyName);
  if (!key) return null;
  const sig = signals[key];
  if (!sig) return null;

  const showRejections = sig.rejections >= 1;
  const showActive = sig.active_apps >= 1;
  if (!showRejections && !showActive) return null;

  // ≥3 still-alive applications at one company is the advisory "you're stacking
  // up here" signal — amber. 1–2 is just informational.
  const activeAmber = sig.active_apps >= 3;

  return (
    <span
      data-testid="repeat-signal-badges"
      className={cn('inline-flex flex-wrap items-center gap-1', className)}
    >
      {showActive && (
        <Badge
          tone={activeAmber ? 'amber' : 'neutral'}
          dataState={activeAmber ? 'amber' : 'neutral'}
          label={`${sig.active_apps} active apps`}
          title={
            activeAmber
              ? `You already have ${sig.active_apps} still-alive applications at this company — consider whether to add another.`
              : `You have ${sig.active_apps} still-alive application${
                  sig.active_apps === 1 ? '' : 's'
                } at this company.`
          }
        />
      )}
      {showRejections && (
        <Badge
          tone="neutral"
          dataState="rejections"
          label={`${sig.rejections} rejection${sig.rejections === 1 ? '' : 's'} here`}
          title={`You have ${sig.rejections} rejection${
            sig.rejections === 1 ? '' : 's'
          } from this company.`}
        />
      )}
    </span>
  );
}

function Badge({
  tone,
  dataState,
  label,
  title,
}: {
  tone: 'neutral' | 'amber';
  dataState: string;
  label: string;
  title: string;
}) {
  const cls =
    tone === 'amber'
      ? 'bg-amber-500/15 text-amber-600 ring-amber-500/30'
      : 'bg-muted text-muted-foreground ring-border';
  return (
    <span
      data-signal={dataState}
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
