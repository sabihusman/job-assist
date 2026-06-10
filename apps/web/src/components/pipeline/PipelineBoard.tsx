import { PipelineColumn } from '@/components/pipeline/PipelineColumn';
import type { RepeatSignals } from '@/lib/api/companySignals';
import { PIPELINE_STAGES, type PipelineStage } from '@/lib/applied/stages';
import type { Buckets } from '@/lib/pipeline/bucket';

/**
 * Horizontally-scrolling kanban board. Column order is presentational only
 * (feat/pipeline-reorder) — `order` reorders the render array; `buckets` are
 * assigned by stage independent of order, so reordering never moves a card
 * between columns. Defaults to the canonical PIPELINE_STAGES order.
 */
export function PipelineBoard({
  buckets,
  order = PIPELINE_STAGES,
  onSelect,
  onMove,
  signals,
}: {
  buckets: Buckets;
  order?: readonly PipelineStage[];
  onSelect?: (cardId: string) => void;
  onMove?: (stage: PipelineStage, dir: 'up' | 'down') => void;
  signals?: RepeatSignals;
}) {
  return (
    <div className="overflow-x-auto">
      <div className="flex flex-row gap-3 p-4">
        {order.map((stage, i) => (
          <PipelineColumn
            key={stage}
            stage={stage}
            cards={buckets[stage]}
            onSelect={onSelect}
            onMove={onMove ? (dir) => onMove(stage, dir) : undefined}
            canMoveEarlier={i > 0}
            canMoveLater={i < order.length - 1}
            signals={signals}
          />
        ))}
      </div>
    </div>
  );
}
