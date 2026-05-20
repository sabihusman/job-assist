'use client';

import { Play } from 'lucide-react';
import { useState } from 'react';

import { ResponsePanel } from '@/components/settings/ResponsePanel';
import { type AdminJobKey, useRunAdminJob } from '@/lib/api/settings';

/**
 * One row in the Manual Jobs section. Three variants are pre-defined
 * in `ManualJobsSection.tsx`:
 *
 *   - discover-ats          — no input
 *   - gmail-backfill        — no input
 *   - greenhouse-ingest     — text input for `{handle}`
 *
 * State: idle → running → response. The ResponsePanel renders inline
 * below the row; dismissing collapses back to idle.
 */
export function ManualJobRow({
  title,
  endpoint,
  job,
  inputPlaceholder,
}: {
  title: string;
  endpoint: string;
  job: AdminJobKey;
  inputPlaceholder?: string;
}) {
  const [input, setInput] = useState('');
  const { run, isRunning, response, error, reset } = useRunAdminJob(job);

  const handleRun = () => {
    if (inputPlaceholder && input.trim() === '') return;
    run(input.trim() || undefined);
  };

  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex items-center gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-[13px] font-medium">{title}</span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            POST {endpoint}
          </span>
        </div>
        {inputPlaceholder && (
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={inputPlaceholder}
            aria-label={`${title} input`}
            className="h-8 w-40 rounded-md border border-border bg-input px-2 text-[13px] outline-none focus:border-border-strong"
          />
        )}
        <button
          type="button"
          onClick={handleRun}
          disabled={isRunning || (!!inputPlaceholder && input.trim() === '')}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-surface px-3 text-[13px] hover:bg-accent disabled:opacity-50"
        >
          {isRunning ? (
            'running…'
          ) : (
            <>
              <Play className="h-3 w-3" aria-hidden="true" />
              run
            </>
          )}
        </button>
      </div>

      {error && <p className="mt-2 text-[12px] text-negative">{error}</p>}
      {response !== null && <ResponsePanel response={response} onDismiss={reset} />}
    </div>
  );
}
