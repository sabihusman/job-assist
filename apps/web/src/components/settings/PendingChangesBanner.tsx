'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  type ApplyProgress,
  type ApplyResult,
  MAX_SWEEP_PASSES,
  PENDING_FIELD_LABELS,
  applyPendingChanges,
  usePendingFields,
} from '@/lib/settings/pendingRequeue';

/**
 * D-SETTINGS-REWIRE Part B (B2-B5): persistent banner shown while any of the
 * six stale-on-save fields has been saved but not applied to existing rows.
 *
 * The pending set lives in localStorage (see pendingRequeue.ts), so the
 * banner survives reloads until a re-run SUCCEEDS. One button runs the
 * two-step apply sequence; per-step progress renders inline; on failure the
 * failing step is named and the pending flag stays set.
 */

type Phase =
  | { kind: 'idle' }
  | { kind: 'running'; progress: ApplyProgress }
  | { kind: 'done'; result: ApplyResult };

function progressLabel(p: ApplyProgress): string {
  if (p.step === 'reeval') return 'Step 1/2 — re-evaluating hard rules…';
  const suffix = p.changed !== undefined ? ` — ${p.changed} changed this pass` : '…';
  return `Step 2/2 — rescoring, pass ${p.pass}/${MAX_SWEEP_PASSES}${suffix}`;
}

export function PendingChangesBanner() {
  const pending = usePendingFields();
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });

  const running = phase.kind === 'running';
  const doneOk = phase.kind === 'done' && phase.result.ok;

  // Nothing pending and nothing to report → no banner. (After a successful
  // apply the pending set is empty but we keep showing the success summary
  // until it is dismissed.)
  if (pending.length === 0 && !doneOk) return null;

  const onApply = async () => {
    setPhase({ kind: 'running', progress: { step: 'reeval' } });
    const result = await applyPendingChanges((progress) => setPhase({ kind: 'running', progress }));
    if (result.ok) {
      // The queue's verdicts/scores just moved server-side — refetch the
      // triage lists instead of showing stale rows (same invalidation set
      // as useUpdateProfile).
      qc.invalidateQueries({ queryKey: ['postings'] });
      qc.invalidateQueries({ queryKey: ['postings-count'] });
    }
    setPhase({ kind: 'done', result });
  };

  return (
    <section
      aria-live="polite"
      className={
        doneOk
          ? 'mb-4 rounded-md border border-positive/40 bg-positive/10 p-4'
          : 'mb-4 rounded-md border border-pending/50 bg-pending/10 p-4'
      }
    >
      {doneOk && phase.kind === 'done' && phase.result.ok ? (
        <div className="flex items-start justify-between gap-3">
          <p className="text-[13px]">
            ✓ Applied. Hard rules re-evaluated ({phase.result.evaluated} open postings,{' '}
            {phase.result.passed} passing). Rescore{' '}
            {phase.result.converged
              ? `converged after ${phase.result.passes} pass${phase.result.passes === 1 ? '' : 'es'}`
              : `hit the ${MAX_SWEEP_PASSES}-pass cap without converging — re-run if scores look off`}{' '}
            ({phase.result.totalChanged} score{phase.result.totalChanged === 1 ? '' : 's'} moved).
          </p>
          <button
            type="button"
            onClick={() => setPhase({ kind: 'idle' })}
            aria-label="Dismiss apply result"
            className="rounded px-1.5 text-[13px] text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-[13px] font-medium">
            Saved, but not yet live in the triage queue:{' '}
            {pending.map((f) => PENDING_FIELD_LABELS[f]).join(' · ')}
          </p>
          <p className="text-[12px] text-muted-foreground">
            Existing postings keep their old hard-rule verdicts and fit scores until you re-run. New
            ingests already use the saved values.
          </p>
          {phase.kind === 'done' && !phase.result.ok && (
            <p className="text-[12px] text-negative">
              {phase.result.failedStep === 'reeval'
                ? 'Step 1/2 (re-evaluate hard rules) failed'
                : `Step 2/2 (score sweep, pass ${phase.result.pass ?? '?'}) failed`}
              : {phase.result.message} — changes remain pending.
            </p>
          )}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onApply}
              disabled={running}
              className="inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-[13px] hover:bg-accent disabled:opacity-50"
            >
              {running ? 'Applying…' : 'Apply changes to queue.'}
            </button>
            {phase.kind === 'running' && (
              <span className="font-mono text-[12px] text-muted-foreground">
                {progressLabel(phase.progress)}
              </span>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
