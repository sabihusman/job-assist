import { type PipelineStage, STAGE_LABELS } from '@/lib/applied/stages';
import type { Buckets } from '@/lib/pipeline/bucket';

/**
 * CSV of the CURRENT Pipeline view (feat/pipeline-export).
 *
 * The Pipeline is derived entirely client-side: `useAllOutcomes` already loads
 * the FULL outcome set (it pages until complete — no cap), and `bucketOutcomes`
 * groups it into stage columns. So "export what you see" is just this pure
 * serialization of the already-bucketed cards — same data, same bucket logic,
 * same on-screen order (stages in the operator's column order; cards in their
 * displayed order). No backend duplication of the bucketing, so the export
 * can't drift from the board.
 */

const HEADERS = ['stage', 'company', 'role', 'date'] as const;

/** RFC-4180 escape: quote a cell iff it contains a comma, quote, CR or LF. */
function csvCell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/**
 * Serialize the pipeline buckets to CSV. ``order`` is the operator's column
 * order so the export matches the board left-to-right; within a stage the cards
 * keep their on-screen order. Returns header-only when the pipeline is empty.
 */
export function buildPipelineCsv(buckets: Buckets, order: readonly PipelineStage[]): string {
  const lines: string[] = [HEADERS.join(',')];
  for (const stage of order) {
    for (const card of buckets[stage]) {
      const row = [STAGE_LABELS[stage], card.companyName, card.roleTitle, card.appliedAt];
      lines.push(row.map((cell) => csvCell(String(cell ?? ''))).join(','));
    }
  }
  return lines.join('\r\n');
}
