'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import type { OutcomesListResponse } from '@/lib/applied/types';
import type { PostingsListResponse } from '@/lib/triage/types';

/**
 * Hooks used by Applied / Pipeline / Companies. Kept in their own
 * module so the Triage page (which doesn't need them) doesn't pull
 * the symbols into its bundle.
 */

const POSTINGS_KEY = 'postings' as const;
const OUTCOMES_KEY = 'outcomes' as const;

export const appliedKeys = {
  appliedPostings: () => [POSTINGS_KEY, { state: ['applied'] }] as const,
  postingOutcomes: (postingId: string) => [OUTCOMES_KEY, { posting_id: postingId }] as const,
  allOutcomes: (limit: number) => [OUTCOMES_KEY, { limit }] as const,
};

/** All postings the operator has marked applied. */
export function useAppliedPostings() {
  return useQuery({
    queryKey: appliedKeys.appliedPostings(),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: { state: ['applied'], limit: 500, offset: 0 } as never },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

/** Outcomes for one posting. Disabled until `postingId` is non-null. */
export function usePostingOutcomes(postingId: string | null) {
  return useQuery({
    queryKey: postingId ? appliedKeys.postingOutcomes(postingId) : ['outcomes', '__none__'],
    queryFn: async () => {
      if (!postingId) throw new Error('usePostingOutcomes called without postingId');
      const { data, error } = await api.GET('/outcomes', {
        params: { query: { posting_id: postingId, limit: 100, offset: 0 } as never },
      });
      if (error) throw error;
      return data as unknown as OutcomesListResponse;
    },
    enabled: !!postingId,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}

/**
 * All outcome events (paginated to a large `limit`) — used by Pipeline
 * to bucket and by Companies to summarise per-company outcomes.
 */
export function useAllOutcomes(limit = 2000) {
  return useQuery({
    queryKey: appliedKeys.allOutcomes(limit),
    queryFn: async () => {
      const { data, error } = await api.GET('/outcomes', {
        params: { query: { limit, offset: 0 } as never },
      });
      if (error) throw error;
      return data as unknown as OutcomesListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}
