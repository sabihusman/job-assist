'use client';

import { useMemo } from 'react';

import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';
import { bucketPostings, emptyBuckets } from '@/lib/pipeline/bucket';

/**
 * Combines the two GET requests that drive Pipeline into a single
 * memoized bucketing result. react-query handles parallel fetching
 * because both hooks fire on mount; the heavy work is the bucket
 * computation, which memo-keys on the two response references.
 */
export function usePipelineData() {
  const postingsQ = useAppliedPostings();
  const outcomesQ = useAllOutcomes();

  const buckets = useMemo(() => {
    if (!postingsQ.data || !outcomesQ.data) return emptyBuckets();
    return bucketPostings(postingsQ.data.items, outcomesQ.data.items);
  }, [postingsQ.data, outcomesQ.data]);

  return {
    buckets,
    isLoading: postingsQ.isLoading || outcomesQ.isLoading,
    isError: postingsQ.isError || outcomesQ.isError,
    error: postingsQ.error ?? outcomesQ.error,
    refetch: () => {
      postingsQ.refetch();
      outcomesQ.refetch();
    },
  };
}
