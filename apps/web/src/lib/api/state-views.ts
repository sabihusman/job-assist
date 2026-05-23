'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import type { PostingsListResponse } from '@/lib/triage/types';

/**
 * Hooks for the read-only state-filtered list pages (PR #50).
 *
 * Both endpoints share the same shape: ``GET /postings?state=<wire>``.
 * They live in their own module so the Triage / Applied bundles don't
 * pull symbols they don't need.
 *
 * Operator-vocabulary mapping:
 *   /passed   → state=not_interested  (posting_action.action_type)
 *   /rejected → state=rejected        (outcome_event EXISTS, PR #50)
 *
 * The page name is operator vocabulary; the wire value stays canonical.
 * The dual-table semantics live entirely server-side — see the bestiary
 * note in the FastAPI handler.
 */

const POSTINGS_KEY = 'postings' as const;

export const stateViewKeys = {
  passed: () => [POSTINGS_KEY, { state: ['not_interested'] }] as const,
  rejected: () => [POSTINGS_KEY, { state: ['rejected'] }] as const,
};

/**
 * All postings the operator passed on (action_type='not_interested').
 * The reason chip from the pass action ships inline as ``state.reason``
 * on every row — no secondary fetch needed.
 */
export function usePassedPostings() {
  return useQuery({
    queryKey: stateViewKeys.passed(),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: { state: ['not_interested'], limit: 500, offset: 0 } as never },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

/**
 * All postings where a rejection outcome_event landed (the operator's
 * Gmail-classified rejection emails). May be empty in v1 until the
 * Gmail cron starts producing rejection rows.
 */
export function useRejectedPostings() {
  return useQuery({
    queryKey: stateViewKeys.rejected(),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: { state: ['rejected'], limit: 500, offset: 0 } as never },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}
