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
    // fix(audit health split): per-pipeline freshness. curated_fresh replaced
    // recent_success — pre-split, any broad/warm-path success masked a dead
    // curated cron.
    curated_fresh: boolean;
    no_hard_failures: boolean;
    // Ran-without-error semantics: a weekly-cap no-op reads GREEN.
    broad_fresh: boolean;
    not_starved: boolean;
    // feat/llm-health: classifier ran in the last 24h AND embeddings aren't
    // piling up exhausted errors.
    llm_healthy: boolean;
    // feat/gmail-health-check: a Gmail sweep started within the last 13h AND the
    // last one didn't fail.
    gmail_healthy: boolean;
    // feat/warm-path-ingest: the weekly alumni-cohort sweep ran within ~9 days
    // (trivially true while no warm-path companies exist).
    warm_path_fresh: boolean;
  };
  metrics: {
    last_success_at: string | null;
    failed_runs_recent: number;
    handle_not_found_recent: number;
    // fix(audit health split): per-pipeline freshness metrics.
    curated_companies: number;
    curated_last_swept_at: string | null;
    broad_last_swept_at: string | null;
    broad_qualified_this_week: number;
    broad_weekly_cap: number;
    broad_cap_met: boolean;
    reclassify_pending: number;
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
    // feat/gmail-health-check: the last Gmail sweep's start, status, and runtime.
    gmail_last_sweep_at: string | null;
    gmail_last_sweep_status: string | null;
    gmail_last_sweep_runtime_seconds: number | null;
    gmail_stale_hours: number;
    // feat/warm-path-ingest: weekly alumni-cohort sweep freshness.
    warm_path_companies: number;
    warm_path_last_swept_at: string | null;
    warm_path_stale_days: number;
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
