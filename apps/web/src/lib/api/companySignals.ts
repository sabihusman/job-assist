'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';

/**
 * Per-company application-awareness signals (feat/company-app-awareness) —
 * computed server-side from the Gmail outcome history and keyed by the
 * NORMALIZED company name (so the unlinked majority of outcomes is captured, and
 * "Stripe, Inc." / "stripe" collapse). One cached fetch feeds every badge
 * (Triage list + detail + Pipeline) via React Query dedup. Consumers look a
 * company up by re-normalizing its display name (see ``normalizeCompanyName``).
 */
export type CompanySignal = {
  rejections: number;
  active_apps: number;
  display_name?: string;
};
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
