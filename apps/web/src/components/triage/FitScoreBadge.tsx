import { cn } from '@/lib/utils';

/**
 * Inline fit-score badge for the Triage card meta row (PR #57).
 *
 * Renders nothing for NULL scores — postings the PR #56 score sweep
 * hasn't visited yet stay un-decorated rather than showing a placeholder
 * zero. The number is shown bare (no "Fit:" prefix) to keep the badge
 * compact at 380px mobile viewports; the semantic label lives in
 * ``aria-label`` and the ``title`` tooltip for both screen-reader users
 * and mouse-hover discovery.
 *
 * Color tones follow PR #56's bucket lookup table:
 *   80-100 → positive (green) — strong fit
 *   60-79  → pending  (amber) — decent fit
 *   40-59  → muted    (grey)  — weak fit
 *    0-39  → muted at 70% opacity — poor fit (intentionally desaturated)
 *
 * The same ``bg-X/15 text-X ring-X/30 ring-1 ring-inset`` pattern as the
 * tier and ATS badges in TriageCard keeps the visual hierarchy
 * coherent.
 */

type Tone = 'positive' | 'pending' | 'muted' | 'muted-dim';

const TONE_CLASSES: Record<Tone, string> = {
  positive: 'bg-positive/15 text-positive ring-positive/30',
  pending: 'bg-pending/15 text-pending ring-pending/30',
  muted: 'bg-muted text-muted-foreground ring-border',
  // Same muted palette but with reduced text opacity — signals "poor fit"
  // without inventing a new semantic token.
  'muted-dim': 'bg-muted text-muted-foreground/70 ring-border',
};

export function toneForScore(score: number): Tone {
  if (score >= 80) return 'positive';
  if (score >= 60) return 'pending';
  if (score >= 40) return 'muted';
  return 'muted-dim';
}

export function FitScoreBadge({
  score,
  className,
}: {
  score: number | null;
  className?: string;
}) {
  if (score === null || score === undefined) {
    return null;
  }
  const tone = toneForScore(score);
  const ariaLabel = `Fit score: ${score} out of 100`;
  return (
    <span
      data-testid="fit-score-badge"
      aria-label={ariaLabel}
      title={ariaLabel}
      className={cn(
        'rounded px-1.5 py-0 font-mono text-[10px] font-medium ring-1 ring-inset',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {score}
    </span>
  );
}
