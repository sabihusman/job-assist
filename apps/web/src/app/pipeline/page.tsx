'use client';

import { useMemo, useState } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { PipelineBoard } from '@/components/pipeline/PipelineBoard';
import { PipelineDetailPanel } from '@/components/pipeline/PipelineDetailPanel';
import { usePipelineData } from '@/lib/api/pipeline';
import { PIPELINE_STAGES, sanitizeColumnOrder } from '@/lib/applied/stages';
import { useUiStore } from '@/lib/stores/ui';

/**
 * Pipeline page. Outcome-driven kanban (feat/pipeline-outcome-cards): buckets
 * are derived from the cached outcomes. feat/pipeline-detail: clicking a card
 * opens the PipelineDetailPanel. feat/pipeline-reorder: column order is a
 * persisted UI pref, sanitized on read so a stale value degrades gracefully.
 */
export default function PipelinePage() {
  const { buckets, isLoading, isError, error, refetch } = usePipelineData();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const rawOrder = useUiStore((s) => s.pipelineColumnOrder);
  const movePipelineColumn = useUiStore((s) => s.movePipelineColumn);
  const order = useMemo(() => sanitizeColumnOrder(rawOrder), [rawOrder]);

  const allEmpty = useMemo(() => PIPELINE_STAGES.every((s) => buckets[s].length === 0), [buckets]);

  return (
    <AppShell title="Pipeline" subtitle="Kanban by outcome stage">
      {isError ? (
        <ErrorCard message={(error as Error)?.message ?? 'Unknown error'} onRetry={refetch} />
      ) : isLoading ? (
        <Skeleton />
      ) : allEmpty ? (
        <EmptyState />
      ) : (
        <div className="flex">
          <div className="min-w-0 flex-1">
            <PipelineBoard
              buckets={buckets}
              order={order}
              onSelect={setSelectedId}
              onMove={movePipelineColumn}
            />
          </div>
          <PipelineDetailPanel selectedId={selectedId} onClose={() => setSelectedId(null)} />
        </div>
      )}
    </AppShell>
  );
}

function Skeleton() {
  return (
    <div className="flex gap-3 p-4 overflow-x-auto">
      {PIPELINE_STAGES.map((s) => (
        <div
          key={s}
          className="h-64 w-64 shrink-0 animate-pulse rounded-md border border-border bg-surface-2"
        />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <section
      data-testid="pipeline-empty"
      className="mx-auto mt-12 flex max-w-md flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
    >
      <h2 className="text-sm font-semibold">No applications yet.</h2>
      <p className="text-[13px] text-muted-foreground">
        Apply to a few postings to see your pipeline take shape.
      </p>
    </section>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="m-6 rounded-md border border-negative/40 bg-negative/5 p-4">
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load pipeline.</h2>
      <p className="mt-1 text-[13px] text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
      >
        Retry
      </button>
    </section>
  );
}
