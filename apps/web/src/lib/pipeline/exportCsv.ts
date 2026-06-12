import { type PipelineStage, STAGE_LABELS } from '@/lib/applied/stages';
import { buildCsv } from '@/lib/csv';
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
 *
 * feat/view-exports: serialization now rides the shared `buildCsv` (same
 * RFC-4180 escaping, byte-identical output) so every tab's export agrees.
 */

const HEADERS = ['stage', 'company', 'role', 'date'] as const;

/**
 * Serialize the pipeline buckets to CSV. ``order`` is the operator's column
 * order so the export matches the board left-to-right; within a stage the cards
 * keep their on-screen order. Returns header-only when the pipeline is empty.
 */
export function buildPipelineCsv(buckets: Buckets, order: readonly PipelineStage[]): string {
  const rows: string[][] = [];
  for (const stage of order) {
    for (const card of buckets[stage]) {
      rows.push([STAGE_LABELS[stage], card.companyName, card.roleTitle, card.appliedAt]);
    }
  }
  return buildCsv(HEADERS, rows);
}
