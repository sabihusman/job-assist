import { cn } from '@/lib/utils';

/**
 * The score-forward block — the dominant left rail of a triage card and the
 * hero of the detail panel header (score-forward restyle).
 *
 * Bands key off OUR real fit score (``posting.score`` / ``fit_score``):
 *   - high ≥ 85  → vivid green   (``--score-high``)
 *   - mid 41–84  → warm sand     (``--score-mid``)
 *   - low ≤ 40   → gray          (``--score-low``) + the caller dims the card
 *
 * (The reference spec wrote "mid 55–84 / low ≤40"; 41–54 was left
 * unspecified, so mid covers 41–84 here — every score maps to exactly one
 * band.) NULL scores (the PR #56 sweep hasn't visited the row) render a
 * neutral muted block with an em-dash, never a misleading "0".
 *
 * This is a NEW component; the legacy inline ``FitScoreBadge`` (old
 * 80/60/40 buckets, its own test) is left untouched.
 */

export type ScoreBand = 'high' | 'mid' | 'low';

const HIGH_MIN = 85;
const MID_MIN = 41;
const DIM_MAX = 40;

/** Band for a known numeric score. */
export function scoreBand(score: number): ScoreBand {
  if (score >= HIGH_MIN) return 'high';
  if (score >= MID_MIN) return 'mid';
  return 'low';
}

/** Low-score rows (≤40) are dimmed so a wall of 40s reads as dismissible. */
export function isDimScore(score: number | null | undefined): boolean {
  return typeof score === 'number' && score <= DIM_MAX;
}

const BAND_CLASSES: Record<ScoreBand, string> = {
  high: 'bg-score-high text-score-high-fg',
  mid: 'bg-score-mid text-score-mid-fg',
  low: 'bg-score-low text-score-low-fg',
};

const SIZE: Record<'md' | 'lg', { box: string; num: string; label: string }> = {
  // Card rail — ~72px, fills the card height (self-stretch via the parent).
  md: { box: 'w-[72px] min-h-full px-2 py-3', num: 'text-2xl', label: 'text-2xs' },
  // Detail header — larger hero block.
  lg: {
    box: 'w-[96px] min-h-[96px] px-3 py-4',
    num: 'text-[2rem] leading-none',
    label: 'text-2xs',
  },
};

export function ScoreBlock({
  score,
  size = 'md',
  showLabel = false,
  className,
}: {
  score: number | null;
  size?: 'md' | 'lg';
  /** Render the "fit score" caption under the number (detail header). */
  showLabel?: boolean;
  className?: string;
}) {
  const dims = SIZE[size];
  const hasScore = typeof score === 'number';
  const bandClass = hasScore ? BAND_CLASSES[scoreBand(score)] : 'bg-muted text-muted-foreground';
  const ariaLabel = hasScore ? `Fit score: ${score} out of 100` : 'Fit score: not yet scored';

  return (
    <div
      data-testid="score-block"
      data-band={hasScore ? scoreBand(score) : 'none'}
      aria-label={ariaLabel}
      title={ariaLabel}
      className={cn(
        'flex shrink-0 flex-col items-center justify-center gap-0.5 text-center',
        dims.box,
        bandClass,
        className,
      )}
    >
      <span className={cn('font-mono font-semibold tabular-nums', dims.num)}>
        {hasScore ? score : '—'}
      </span>
      {showLabel && (
        <span className={cn('font-mono uppercase tracking-wide opacity-80', dims.label)}>
          fit score
        </span>
      )}
    </div>
  );
}
