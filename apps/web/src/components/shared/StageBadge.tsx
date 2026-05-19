import { type PipelineStage, STAGE_LABELS, stageBadgeTone } from '@/lib/applied/stages';
import { cn } from '@/lib/utils';

/**
 * Coarse-grained outcome stage badge. Used in:
 *   - Applied row (far right + each timeline event)
 *   - DetailPanel state badges (future)
 *
 * The hue is driven by `stageBadgeTone` so the four-token palette
 * (positive / negative / pending / muted) stays consistent.
 */
const TONE_CLASSES = {
  positive: 'bg-positive/15 text-positive ring-positive/30',
  negative: 'bg-negative/15 text-negative ring-negative/30',
  pending: 'bg-pending/15 text-pending ring-pending/30',
  muted: 'bg-muted text-muted-foreground ring-border',
} as const;

export function StageBadge({
  stage,
  className,
}: {
  stage: PipelineStage;
  className?: string;
}) {
  const tone = stageBadgeTone(stage);
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0 text-[10px] font-medium ring-1 ring-inset',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {STAGE_LABELS[stage]}
    </span>
  );
}
