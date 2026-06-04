'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';

/**
 * Wire shape for `GET /stats/ingest` (feat/ingest-visibility). The endpoint
 * returns `dict[str, Any]`, so the generated openapi type is wide — pinned here.
 */
export type IngestStats = {
  window_days: number;
  totals: { runs: number; successes: number; failures: number; postings_new: number };
  daily: {
    day: string;
    postings_new: number;
    postings_fetched: number;
    runs: number;
    failures: number;
  }[];
  by_source: {
    source: string;
    status: string;
    last_run_at: string;
    postings_new: number;
  }[];
};

/** Ingest health for the Stats panel — daily new-posting counts + per-source
 *  last status over the last `days` (1-30). */
export function useIngestStats(days = 14) {
  return useQuery({
    queryKey: ['ingest-stats', days] as const,
    queryFn: async () => {
      const { data, error } = await api.GET('/stats/ingest', {
        params: { query: { days } as never },
      });
      if (error) throw error;
      return data as unknown as IngestStats;
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}
