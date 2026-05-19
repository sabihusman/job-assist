'use client';

import { useMemo } from 'react';

import { AppliedRow } from '@/components/applied/AppliedRow';
import { useAllOutcomes } from '@/lib/api/applied';
import { type PipelineStage, STAGE_SORT_ORDER, stageOf } from '@/lib/applied/stages';
import type { AppliedSort } from '@/lib/applied/types';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Sorted list of AppliedRow. Each posting's "current stage" is derived
 * from its latest outcome event (if any) using `stageOf`; falls back
 * to the `applied` stage so the timeline always renders at minimum.
 *
 * `useAllOutcomes` is shared with Pipeline/Companies so navigating
 * between those pages doesn't trigger a refetch.
 */

export type SortedAppliedRow = {
  posting: PostingListItem;
  currentStage: PipelineStage;
  appliedAt: number; // ms epoch — keyed for sort=applied
};

const GHOSTED_AFTER_DAYS = 30;
const GHOSTED_AFTER_MS = GHOSTED_AFTER_DAYS * 24 * 60 * 60 * 1000;

export function AppliedList({
  postings,
  sort,
}: {
  postings: readonly PostingListItem[];
  sort: AppliedSort;
}) {
  const { data: outcomes } = useAllOutcomes();

  const rows = useMemo<SortedAppliedRow[]>(() => {
    // Index outcomes by posting_id once for O(1) lookup per row.
    const byPosting = new Map<string, { latest: { stage: PipelineStage; ts: number } | null }>();
    for (const o of outcomes?.items ?? []) {
      if (!o.posting_id) continue;
      const stage = stageOf(o.stage);
      if (!stage) continue;
      const ts = Date.parse(o.received_at);
      const entry = byPosting.get(o.posting_id);
      if (!entry || !entry.latest || ts > entry.latest.ts) {
        byPosting.set(o.posting_id, { latest: { stage, ts } });
      }
    }

    const now = Date.now();
    return postings.map((p) => {
      const appliedAtIso = p.state.current_at ?? p.first_seen_at;
      const appliedAt = Date.parse(appliedAtIso);
      const latest = byPosting.get(p.id)?.latest ?? null;
      let currentStage: PipelineStage;
      if (latest) {
        currentStage = latest.stage;
      } else if (now - appliedAt > GHOSTED_AFTER_MS) {
        currentStage = 'ghosted';
      } else {
        currentStage = 'applied';
      }
      return { posting: p, currentStage, appliedAt };
    });
  }, [postings, outcomes]);

  const sorted = useMemo(() => {
    const copy = [...rows];
    if (sort === 'applied') {
      copy.sort((a, b) => b.appliedAt - a.appliedAt);
    } else if (sort === 'stage') {
      copy.sort((a, b) => STAGE_SORT_ORDER[a.currentStage] - STAGE_SORT_ORDER[b.currentStage]);
    } else if (sort === 'tier') {
      copy.sort((a, b) => {
        const at = a.posting.company.tier ?? 99;
        const bt = b.posting.company.tier ?? 99;
        return at - bt;
      });
    }
    return copy;
  }, [rows, sort]);

  if (sorted.length === 0) {
    return (
      <section
        data-testid="applied-empty"
        className="flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center"
      >
        <h2 className="text-sm font-semibold">No active applications.</h2>
        <p className="text-[13px] text-muted-foreground">
          Mark some Triage postings as Applied to see them here.
        </p>
      </section>
    );
  }

  return (
    <ul className="flex list-none flex-col gap-3 p-0">
      {sorted.map(({ posting, currentStage }) => (
        <AppliedRow key={posting.id} posting={posting} currentStage={currentStage} />
      ))}
    </ul>
  );
}
