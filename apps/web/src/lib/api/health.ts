'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';

/**
 * System-health hook for the sidebar dot (feat/health-indicator).
 *
 * Polls GET /admin/ingest/health every 60s. The endpoint computes a three-state
 * ``severity`` server-side: ``ok`` (green), ``degraded`` (yellow — soft problem
 * like starvation / a stale broad set), ``down`` (red — a cron failed or didn't
 * run). The frontend maps a FETCH ERROR (unreachable backend) to ``down`` too —
 * a dead backend must never read green.
 */

export type HealthSeverity = 'ok' | 'degraded' | 'down';

export type IngestHealth = {
  ok: boolean;
  severity: HealthSeverity;
  problems: string[];
  checks: {
    recent_success: boolean;
    no_hard_failures: boolean;
    broad_fresh: boolean;
    not_starved: boolean;
    // feat/llm-health: classifier ran in the last 24h AND embeddings aren't
    // piling up exhausted errors.
    llm_healthy: boolean;
  };
  metrics: {
    last_success_at: string | null;
    failed_runs_recent: number;
    handle_not_found_recent: number;
    broad_last_swept_at: string | null;
    net_new_starvation_window: number;
    window_hours: number;
    starvation_days: number;
    // feat/llm-health: most recent Gemini activity (classifier or embedding)
    // and the exhausted-embedding-error count.
    llm_last_used_at: string | null;
    llm_last_classified_at: string | null;
    llm_last_embedded_at: string | null;
    llm_exhausted_errors: number;
    llm_stale_hours: number;
  };
};

export const HEALTH_POLL_MS = 60_000;

export function useIngestHealth() {
  return useQuery({
    queryKey: ['ingest-health'] as const,
    queryFn: async (): Promise<IngestHealth> => {
      const { data, error } = await api.GET('/admin/ingest/health');
      if (error) throw error;
      return data as unknown as IngestHealth;
    },
    // Keep the dot live without a refresh; keep polling even when the tab is
    // backgrounded so a backend going down is caught promptly.
    refetchInterval: HEALTH_POLL_MS,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
    staleTime: HEALTH_POLL_MS / 2,
    // One quick retry smooths a transient blip; a persistent failure still
    // surfaces as isError → red within ~a poll.
    retry: 1,
  });
}
