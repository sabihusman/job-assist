'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { api } from '@/lib/api/client';
import type { OperatorProfileRead, OperatorProfileUpdate } from '@/lib/settings/types';

/**
 * Hooks for the Settings page (PR #32d).
 *
 * `useUpdateProfile` invalidates the GET /operator/profile cache on
 * success so the next render reads fresh values — important because
 * the form prefills from that query.
 *
 * `useRunAdminJob` is a per-row hook the Manual Job rows call to
 * trigger admin endpoints. It manages its own `running` and
 * `response` state so each row is independent.
 */

const PROFILE_KEY = ['operator-profile'] as const;

export function useOperatorProfile() {
  return useQuery({
    queryKey: PROFILE_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET('/operator/profile', {});
      if (error) throw error;
      return data as unknown as OperatorProfileRead;
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: OperatorProfileUpdate) => {
      const { data, error } = await api.PUT('/operator/profile', {
        body: body as never,
      });
      if (error) throw error;
      return data as unknown as OperatorProfileRead;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROFILE_KEY });
      // A profile save can re-score the whole open corpus server-side (a
      // looking_for_text edit, or an applied_corpus_weight change firing the
      // A3 rescore). Invalidate the postings/count caches so triage & pipeline
      // refetch the updated rankings instead of showing stale scores.
      qc.invalidateQueries({ queryKey: ['postings'] });
      qc.invalidateQueries({ queryKey: ['postings-count'] });
    },
  });
}

// ── Admin jobs ──────────────────────────────────────────────────────────

// D-SETTINGS-REWIRE C3: the backfill endpoint is async (202 + job row).
// Poll cadence/cap for GET /admin/gmail/jobs/{job_id} — the sweep runs
// 5-10 minutes on the Gemini free tier, so cap well above that.
export const GMAIL_POLL_INTERVAL_MS = 3_000;
export const GMAIL_POLL_MAX_POLLS = 400; // 20 minutes at 3s

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Poll the gmail sweep job row until it leaves 'running'. Returns the final
 * row on success; throws with the row's error_message on failure, and throws
 * (leaving the job running server-side) if the poll cap is exhausted.
 */
export async function pollGmailJob(
  jobId: string,
  {
    intervalMs = GMAIL_POLL_INTERVAL_MS,
    maxPolls = GMAIL_POLL_MAX_POLLS,
  }: { intervalMs?: number; maxPolls?: number } = {},
): Promise<unknown> {
  for (let i = 0; i < maxPolls; i++) {
    await sleep(intervalMs);
    const { data, error } = await api.GET('/admin/gmail/jobs/{job_id}', {
      params: { path: { job_id: jobId } },
    });
    if (error) throw new Error(JSON.stringify(error));
    const row = data as { status: string; error_message?: string | null };
    if (row.status === 'failed') throw new Error(row.error_message ?? 'Gmail backfill failed');
    if (row.status !== 'running') return row;
  }
  const cappedMinutes = (intervalMs * maxPolls) / 60_000;
  throw new Error(
    `Backfill still running after ${cappedMinutes} minutes — it continues server-side; check GET /admin/gmail/jobs for the result.`,
  );
}

/**
 * Runs an admin POST endpoint. The caller supplies the static endpoint
 * key + an optional path-param value (used only by the Greenhouse
 * ingestion row, which needs a `handle`).
 *
 * Returns:
 *   - `run(input?)`      — fire the request
 *   - `isRunning`        — true while in-flight
 *   - `response`         — parsed JSON body on success (or null)
 *   - `error`            — error message string on failure
 *   - `reset()`          — clear the response so the row collapses back to idle
 */
export type AdminJobKey = 'gmail-backfill' | 'greenhouse-ingest';

export function useRunAdminJob(job: AdminJobKey) {
  const [response, setResponse] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (input?: string) => {
      if (job === 'gmail-backfill') {
        const { data, error } = await api.POST('/admin/gmail/backfill', {
          params: { query: { days: 60 } as never },
        });
        if (error) throw error;
        // 202 + job row (D-SETTINGS-REWIRE C2/C3): poll until terminal. The
        // mutation stays pending for the whole poll, so the row's button
        // stays disabled while the job is in flight.
        const accepted = data as unknown as { job_id: string };
        return await pollGmailJob(accepted.job_id);
      }
      if (job === 'greenhouse-ingest') {
        if (!input) throw new Error('Greenhouse ingestion requires a handle.');
        const { data, error } = await api.POST('/admin/ingest/{ats}/{handle}', {
          params: { path: { ats: 'greenhouse', handle: input } },
        });
        if (error) throw error;
        return data;
      }
      throw new Error(`Unknown admin job: ${job}`);
    },
    onSuccess: (data) => {
      setResponse(data);
      setError(null);
    },
    onError: (err: Error) => {
      setError(err.message ?? 'Unknown error');
      setResponse(null);
    },
  });

  const run = useCallback(
    (input?: string) => {
      mutation.mutate(input);
    },
    [mutation],
  );

  const reset = useCallback(() => {
    setResponse(null);
    setError(null);
    mutation.reset();
  }, [mutation]);

  return {
    run,
    isRunning: mutation.isPending,
    response,
    error,
    reset,
  };
}
