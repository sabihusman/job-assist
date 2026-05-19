'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';

/**
 * Thin react-query wrappers per public endpoint. Pages should reach for
 * these rather than calling `api.GET(...)` directly so the query keys
 * stay consistent across cache lookups.
 *
 * #32a only wires the smoke-test `usePostings` call (one queryFn that
 * hits `/postings` with the default page size). #32b will add real
 * filter params and an enriched return shape.
 */

export const queryKeys = {
  postings: (params: Record<string, unknown> = {}) => ['postings', params] as const,
};

export function usePostings() {
  return useQuery({
    queryKey: queryKeys.postings(),
    queryFn: async () => {
      const { data, error } = await api.GET('/postings', {
        params: { query: { limit: 20, offset: 0 } },
      });
      if (error) throw error;
      return data;
    },
    // Stop hammering Railway every focus event — refresh on demand only.
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}
