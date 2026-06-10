'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';

/**
 * Per-company repeat signals (feat/repeat-signal-flags) — companies where the
 * operator has 2+ rejections or 2+ still-alive applications, computed server-side
 * from the Gmail outcome history and keyed by ``company_id``. One cached fetch
 * feeds every badge (Triage detail + Pipeline) via React Query dedup.
 */
export type CompanySignal = { rejections: number; active_apps: number };
export type RepeatSignals = Record<string, CompanySignal>;

export function useCompanySignals() {
  return useQuery({
    queryKey: ['company-repeat-signals'],
    queryFn: async (): Promise<RepeatSignals> => {
      const { data, error } = await api.GET('/companies/repeat-signals');
      if (error) throw error;
      return (data as unknown as { signals: RepeatSignals }).signals ?? {};
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}
