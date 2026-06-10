'use client';

import { useCallback } from 'react';

import type { PipelineStage } from '@/lib/applied/stages';
import type { Buckets } from '@/lib/pipeline/bucket';
import { buildPipelineCsv } from '@/lib/pipeline/exportCsv';

/**
 * "Export current view" for the Pipeline (feat/pipeline-export).
 *
 * Mirrors the Triage export's intent — download exactly what's on screen, every
 * row, no cap — but client-side: the board is already derived in the browser
 * from the full outcome set, so we serialize the in-memory buckets rather than
 * round-tripping to a backend endpoint that would have to re-implement (and
 * could drift from) the bucketing logic. Emits a UTF-8 CSV (BOM-prefixed so
 * Excel reads it as UTF-8).
 */
function timestamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

export function PipelineExportButton({
  buckets,
  order,
}: {
  buckets: Buckets;
  order: readonly PipelineStage[];
}) {
  const total = order.reduce((n, stage) => n + buckets[stage].length, 0);

  const onClick = useCallback(() => {
    const csv = buildPipelineCsv(buckets, order);
    // BOM so Excel opens UTF-8 cleanly; Blob keeps the pure builder BOM-free.
    const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pipeline-export-${timestamp()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [buckets, order]);

  return (
    <button
      type="button"
      data-testid="pipeline-export-button"
      onClick={onClick}
      disabled={total === 0}
      title="Download a .csv of every card currently on the board — same stages, same order."
      className="inline-flex shrink-0 items-center gap-1.5 rounded border border-border bg-surface px-2.5 py-1 text-sm text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      Export current view
    </button>
  );
}
