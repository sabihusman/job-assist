'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import type { OutcomesListResponse } from '@/lib/applied/types';
import type { PostingsListResponse } from '@/lib/triage/types';

/**
 * Hooks used by Applied / Pipeline / Companies / Stats. Kept in their
 * own module so the Triage page (which doesn't need them) doesn't pull
 * the symbols into its bundle.
 *
 * Pagination (PR #66): list hooks accept an ``offset`` and use page
 * size 100 (the backend cap). The aggregator ``useAllOutcomes`` runs
 * a single ``queryFn`` that loops 100-at-a-time until it has every
 * row — no UI pagination because the consumers
 * (Pipeline / Companies / Stats) aggregate over the full set.
 *
 * Bestiary 5.11: previously requested ``limit=500`` / ``limit=2000``
 * which the API caps at 100 → 422 → React Query empty fallback →
 * pages silently showed empty state when data existed.
 */

const POSTINGS_KEY = 'postings' as const;
const OUTCOMES_KEY = 'outcomes' as const;
const PAGE_SIZE = 100;

export const appliedKeys = {
  appliedPostings: (offset = 0) => [POSTINGS_KEY, { state: ['applied'], offset }] as const,
  postingOutcomes: (postingId: string) => [OUTCOMES_KEY, { posting_id: postingId }] as const,
  /** Full outcome set. `jobRelated` varies the key so the Pipeline's
   *  filtered fetch doesn't collide with Companies/Stats' full fetch. */
  allOutcomes: (jobRelated = false) => [OUTCOMES_KEY, { scope: 'all', jobRelated }] as const,
};

/**
 * Page of postings the operator has marked applied.
 *
 * ``enabled`` lets the page conditionally fire a second query for
 * Load-More-driven extra pages.
 */
export function useAppliedPostings(offset = 0, enabled = true) {
  return useQuery({
    queryKey: appliedKeys.appliedPostings(offset),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: { state: ['applied'], limit: PAGE_SIZE, offset } as never },
      });
      if (error) throw error;
      return data as unknown as PostingsListResponse;
    },
    enabled,
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
        params: { query: { posting_id: postingId, limit: PAGE_SIZE, offset: 0 } as never },
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
 * All outcome events — used by Pipeline (bucket-by-stage), Companies
 * (per-company summary), and Stats (funnel). All three need the full
 * set; pagination UI doesn't apply here.
 *
 * Implementation: single ``useQuery``; the ``queryFn`` loops 100-at-a-time
 * until the cumulative ``items.length`` matches the server's reported
 * ``total`` (or a short page signals end-of-set). One cache entry, one
 * isLoading window. Trade-off: latency scales linearly with outcome
 * volume — at ~50–100 rows today this is 1–2 round-trips. If volume
 * grows past ~1000 rows the page-load latency becomes noticeable;
 * escalate to a server-side aggregator endpoint as its own PR.
 */
export function useAllOutcomes(jobRelated = false) {
  return useQuery({
    queryKey: appliedKeys.allOutcomes(jobRelated),
    queryFn: async () => {
      const all: OutcomesListResponse['items'] = [];
      let offset = 0;
      let total = 0;
      // Defensive cap — protects against a runaway loop if the API ever
      // returns total=N but never advances. At PAGE_SIZE=100, this caps
      // at 100k outcome rows, which is well above any realistic volume.
      for (let i = 0; i < 1000; i++) {
        const { data, error } = await api.GET('/outcomes', {
          params: {
            query: { limit: PAGE_SIZE, offset, ...(jobRelated && { job_related: true }) } as never,
          },
        });
        if (error) throw error;
        const page = data as unknown as OutcomesListResponse;
        total = page.total;
        all.push(...page.items);
        // Two end-of-set signals: short page (server gave us < PAGE_SIZE),
        // OR cumulative count reached total. Both are belt-and-braces.
        if (page.items.length < PAGE_SIZE) break;
        if (all.length >= total) break;
        offset += page.items.length;
      }
      return {
        total,
        offset: 0,
        limit: all.length,
        items: all,
      } satisfies OutcomesListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}
