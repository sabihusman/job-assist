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
 *
 * Pagination (PR #66): hooks accept an ``offset`` and use page size 100
 * (the backend cap). Pages use the Load More pattern — render page 1
 * unconditionally, then a second instance of the hook fetches additional
 * pages when the operator clicks "Load more". Mirrors
 * ``OutreachTimeline.tsx``.
 *
 * Bestiary 5.11: previously these hooks requested ``limit=500`` which
 * the API caps at 100 → 422 → React Query empty fallback → page rendered
 * "No passed postings yet" even when rows existed. The page-level error
 * card now surfaces non-2xx responses explicitly.
 */

const POSTINGS_KEY = 'postings' as const;
const PAGE_SIZE = 100;

export const stateViewKeys = {
  passed: (offset = 0) => [POSTINGS_KEY, { state: ['not_interested'], offset }] as const,
  rejected: (offset = 0) => [POSTINGS_KEY, { state: ['rejected'], offset }] as const,
};

/**
 * Page of postings the operator passed on (action_type='not_interested').
 * The reason chip from the pass action ships inline as ``state.reason``
 * on every row — no secondary fetch needed.
 *
 * ``enabled`` lets the page conditionally fire a second query for
 * Load-More-driven extra pages without firing on initial mount.
 */
export function usePassedPostings(offset = 0, enabled = true) {
  return useQuery({
    queryKey: stateViewKeys.passed(offset),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: {
          query: { state: ['not_interested'], limit: PAGE_SIZE, offset } as never,
        },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

/**
 * Page of postings where a rejection outcome_event landed (the
 * operator's Gmail-classified rejection emails). May be empty in v1
 * until the Gmail cron starts producing rejection rows.
 */
export function useRejectedPostings(offset = 0, enabled = true) {
  return useQuery({
    queryKey: stateViewKeys.rejected(offset),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: {
          query: { state: ['rejected'], limit: PAGE_SIZE, offset } as never,
        },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}
