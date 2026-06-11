import Link from 'next/link';

import type { RepeatSignals } from '@/lib/api/companySignals';
import { normalizeCompanyName } from '@/lib/pipeline/normalizeCompanyName';
import { cn } from '@/lib/utils';

/**
 * Company-level application-awareness badges (feat/company-app-awareness +
 * feat/warm-path-badge). Pure + presentational: it takes the already-fetched
 * ``signals`` map (one cached `useCompanySignals` fetch lives at the page /
 * detail-panel level and is passed down) so leaf components like TriageCard /
 * PipelineCard don't each couple to React Query. The map is keyed by NORMALIZED
 * company name, so the badge re-normalizes ``companyName`` to look it up.
 * Renders nothing when the company is unknown or has no signal.
 *
 * Badges (advisory only — never blocking):
 *   • alumni contacts ≥1 → POSITIVE "N alumni here"  (the warm path in)
 *   • active apps  1–2  → NEUTRAL "N active apps"   (informational)
 *   • active apps  ≥3   → AMBER   "N active apps"    (warning: portfolio heavy)
 *   • rejections   ≥1   → NEUTRAL "N rejections here"
 *   • 0 on an axis      → no badge for that axis
 *
 * ``linkToContacts``: when true, the alumni badge is a link to the Contacts
 * page filtered to this company (/contacts?company=…). Only the DetailPanel
 * sets it — on TriageCard/PipelineCard the badges sit INSIDE an interactive
 * card (a <button> / li[role=button]), where a nested anchor is invalid, so
 * those surfaces render the same badge as a plain span.
 *
 * ``size``: the two surfaces deliberately differ — ``sm`` (default) is the
 * dense list/pipeline-card scale; ``lg`` is ~1.5x (font, padding, gap) for
 * the DetailPanel hero where the badges are a primary signal, not a hint.
 * The variant applies to the WHOLE row so the alumni/apps/rejections badges
 * stay visually consistent.
 */
export function RepeatSignalBadges({
  companyName,
  signals,
  linkToContacts = false,
  size = 'sm',
  className,
}: {
  companyName: string | null | undefined;
  signals: RepeatSignals | undefined;
  linkToContacts?: boolean;
  size?: 'sm' | 'lg';
  className?: string;
}) {
  if (!companyName || !signals) return null;
  const key = normalizeCompanyName(companyName);
  if (!key) return null;
  const sig = signals[key];
  if (!sig) return null;

  const contactCount = sig.contact_count ?? 0;
  const showAlumni = contactCount >= 1;
  const showRejections = sig.rejections >= 1;
  const showActive = sig.active_apps >= 1;
  if (!showAlumni && !showRejections && !showActive) return null;

  // ≥3 still-alive applications at one company is the advisory "you're stacking
  // up here" signal — amber. 1–2 is just informational.
  const activeAmber = sig.active_apps >= 3;

  return (
    <span
      data-testid="repeat-signal-badges"
      className={cn(
        'inline-flex flex-wrap items-center',
        size === 'lg' ? 'gap-1.5' : 'gap-1',
        className,
      )}
    >
      {showAlumni && (
        <Badge
          tone="positive"
          size={size}
          dataState="alumni"
          label={`${contactCount} ${contactCount === 1 ? 'alum' : 'alumni'} here`}
          title={`You have ${contactCount} alumni contact${
            contactCount === 1 ? '' : 's'
          } at this company — a warm path in. Click to view them.`}
          href={
            linkToContacts
              ? `/contacts?company=${encodeURIComponent(sig.display_name ?? companyName)}`
              : undefined
          }
        />
      )}
      {showActive && (
        <Badge
          tone={activeAmber ? 'amber' : 'neutral'}
          size={size}
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
          size={size}
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

const TONE_CLASSES = {
  positive: 'bg-positive/15 text-positive ring-positive/30',
  amber: 'bg-amber-500/15 text-amber-600 ring-amber-500/30',
  neutral: 'bg-muted text-muted-foreground ring-border',
} as const;

// Size variants — sm is the dense list/pipeline-card scale (the original);
// lg is ~1.5x for the DetailPanel hero (15px font, 10px x-pad, real y-pad).
const SIZE_CLASSES = {
  sm: 'rounded px-1.5 py-0 text-[10px]',
  lg: 'rounded-md px-2.5 py-0.5 text-[15px]',
} as const;

function Badge({
  tone,
  size,
  dataState,
  label,
  title,
  href,
}: {
  tone: keyof typeof TONE_CLASSES;
  size: keyof typeof SIZE_CLASSES;
  dataState: string;
  label: string;
  title: string;
  href?: string;
}) {
  const cls = cn(
    'inline-flex w-fit items-center font-mono font-medium uppercase tracking-wide ring-1 ring-inset',
    SIZE_CLASSES[size],
    TONE_CLASSES[tone],
    href && 'underline-offset-2 hover:underline',
  );
  if (href) {
    return (
      <Link data-signal={dataState} href={href} title={title} className={cls}>
        {label}
      </Link>
    );
  }
  return (
    <span data-signal={dataState} title={title} className={cls}>
      {label}
    </span>
  );
}
