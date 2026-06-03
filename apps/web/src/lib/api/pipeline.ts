'use client';

import { useMemo } from 'react';

import { useAllOutcomes } from '@/lib/api/applied';
import { bucketOutcomes, emptyBuckets } from '@/lib/pipeline/bucket';

/**
 * Drives the Pipeline kanban from **outcome_events** (feat/pipeline-outcome-
 * cards). The operator's job-search history lives entirely in `outcome_event`
 * (Gmail crawl); the old applied-postings source was empty (0 in-app "applied"
 * actions) and dropped every outcome on a NULL `posting_id`, so the page
 * always rendered empty. We now fetch the job-related outcomes and bucket them
 * into cards client-side.
 */
export function usePipelineData() {
  const outcomesQ = useAllOutcomes(true);

  const buckets = useMemo(() => {
    if (!outcomesQ.data) return emptyBuckets();
    return bucketOutcomes(outcomesQ.data.items);
  }, [outcomesQ.data]);

  return {
    buckets,
    isLoading: outcomesQ.isLoading,
    isError: outcomesQ.isError,
    error: outcomesQ.error,
    refetch: () => {
      outcomesQ.refetch();
    },
  };
}
