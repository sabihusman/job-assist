import { PipelineColumn } from '@/components/pipeline/PipelineColumn';
import { PIPELINE_STAGES } from '@/lib/applied/stages';
import type { Buckets } from '@/lib/pipeline/bucket';

/**
 * Horizontally-scrolling 8-column board. Columns order is fixed per
 * UI_SPEC.md (APPLIED → RECRUITER → PHONE → VIDEO → ONSITE → OFFER →
 * REJECTED → GHOSTED) and is sourced from `PIPELINE_STAGES`.
 */
export function PipelineBoard({ buckets }: { buckets: Buckets }) {
  return (
    <div className="overflow-x-auto">
      <div className="flex flex-row gap-3 p-4">
        {PIPELINE_STAGES.map((stage) => (
          <PipelineColumn key={stage} stage={stage} cards={buckets[stage]} />
        ))}
      </div>
    </div>
  );
}
