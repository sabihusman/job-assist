import { PipelineColumn } from '@/components/pipeline/PipelineColumn';
import {
  DEFAULT_PIPELINE_SORT,
  type PipelineSort,
} from '@/components/pipeline/PipelineSortControl';
import type { RepeatSignals } from '@/lib/api/companySignals';
import { PIPELINE_STAGES, type PipelineStage } from '@/lib/applied/stages';
import type { ApplicationCard, Buckets } from '@/lib/pipeline/bucket';

/**
 * Sort a column's cards by outcome/email date (`appliedAt`), client-side.
 * 'recent' = newest first (default), 'oldest' = oldest first. Returns a NEW
 * array (never mutates the bucket); applied per-column (each column is its own
 * list), so columns sort independently.
 */
function sortCards(cards: readonly ApplicationCard[], sort: PipelineSort): ApplicationCard[] {
  const dir = sort === 'oldest' ? 1 : -1;
  return [...cards].sort((a, b) => dir * (Date.parse(a.appliedAt) - Date.parse(b.appliedAt)));
}

/**
 * Horizontally-scrolling kanban board. Column order is presentational only
 * (feat/pipeline-reorder) — `order` reorders the render array; `buckets` are
 * assigned by stage independent of order, so reordering never moves a card
 * between columns. Defaults to the canonical PIPELINE_STAGES order. `sort`
 * orders each column's cards by date (client-side).
 */
export function PipelineBoard({
  buckets,
  order = PIPELINE_STAGES,
  sort = DEFAULT_PIPELINE_SORT,
  onSelect,
  onMove,
  signals,
}: {
  buckets: Buckets;
  order?: readonly PipelineStage[];
  sort?: PipelineSort;
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
            cards={sortCards(buckets[stage], sort)}
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
