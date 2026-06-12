'use client';

import { useCallback } from 'react';

import { ExportCsvButton } from '@/components/shared/ExportCsvButton';
import type { PipelineStage } from '@/lib/applied/stages';
import type { Buckets } from '@/lib/pipeline/bucket';
import { buildPipelineCsv } from '@/lib/pipeline/exportCsv';

/**
 * "Export current view" for the Pipeline (feat/pipeline-export).
 *
 * Serializes the in-memory buckets — exactly what's on the board, every row,
 * no cap. feat/view-exports: now a thin wrapper over the shared
 * `ExportCsvButton` so all tabs' exports share one download path.
 */
export function PipelineExportButton({
  buckets,
  order,
}: {
  buckets: Buckets;
  order: readonly PipelineStage[];
}) {
  const total = order.reduce((n, stage) => n + buckets[stage].length, 0);
  const build = useCallback(() => buildPipelineCsv(buckets, order), [buckets, order]);

  return (
    <ExportCsvButton
      buildCsv={build}
      filenamePrefix="pipeline-export"
      disabled={total === 0}
      testId="pipeline-export-button"
      title="Download a .csv of every card currently on the board — same stages, same order."
    />
  );
}
