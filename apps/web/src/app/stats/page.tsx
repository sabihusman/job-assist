'use client';

import { useMemo } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { KPICard } from '@/components/stats/KPICard';
import { type FunnelRow, OutcomeFunnel } from '@/components/stats/OutcomeFunnel';
import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';
import { useCalibration } from '@/lib/api/hooks';
import { stageOf } from '@/lib/applied/stages';

/**
 * Stats page (PR #32c).
 *
 * v1 strips:
 *   - "Postings ingested (last 30d)" and "Applications (last 30d)"
 *     cards stripped — would require a second `/stats/calibration`
 *     call with `since=30d`. Document in PR body.
 *   - "Avg time to first response" — ships as "—" placeholder; no
 *     backend support yet.
 *   - SOURCE EFFECTIVENESS panel — stripped per spec.
 *
 * The funnel's deeper interview stages aren't in `/stats/funnel`,
 * so we compute recruiter → phone → video → onsite → offer counts
 * client-side from the `/outcomes` dataset (already cached if the
 * user navigated through Applied / Pipeline first).
 */
export default function StatsPage() {
  const calibrationQ = useCalibration();
  const appliedQ = useAppliedPostings();
  const outcomesQ = useAllOutcomes();

  const funnelRows = useMemo<FunnelRow[]>(() => {
    const applied = appliedQ.data?.items ?? [];
    const outcomes = outcomesQ.data?.items ?? [];

    // Per-stage distinct posting counts. The deeper-stage rows count
    // each posting at most once even if it has multiple events.
    //
    // Use a strict object type rather than ``Record<string, Set<string>>``
    // so TS knows every key is present — avoids the non-null assertions
    // that biome's noNonNullAssertion rule flags.
    type StageKey = 'recruiter' | 'phone' | 'video' | 'onsite' | 'offer';
    const seen: Record<StageKey, Set<string>> = {
      recruiter: new Set(),
      phone: new Set(),
      video: new Set(),
      onsite: new Set(),
      offer: new Set(),
    };
    const isStageKey = (s: string | null): s is StageKey =>
      s === 'recruiter' || s === 'phone' || s === 'video' || s === 'onsite' || s === 'offer';

    for (const o of outcomes) {
      if (!o.posting_id) continue;
      const s = stageOf(o.stage);
      if (isStageKey(s)) seen[s].add(o.posting_id);
    }

    return [
      { stage: 'Applied', count: applied.length },
      { stage: 'Recruiter screen', count: seen.recruiter.size },
      { stage: 'Phone interview', count: seen.phone.size },
      { stage: 'Video interview', count: seen.video.size },
      { stage: 'Onsite', count: seen.onsite.size },
      { stage: 'Offer', count: seen.offer.size },
    ];
  }, [appliedQ.data, outcomesQ.data]);

  const calib = calibrationQ.data;
  const appliedCount = appliedQ.data?.items.length ?? 0;
  const offerCount = funnelRows[5]?.count ?? 0;
  const recruiterPlusCount = funnelRows.slice(1, 5).reduce((sum, row) => sum + row.count, 0);

  const isLoading = calibrationQ.isLoading || appliedQ.isLoading || outcomesQ.isLoading;

  return (
    <AppShell title="Stats" subtitle="Operator metrics">
      <div className="flex flex-col gap-6 px-6 py-4">
        {/* KPI grid — 5 cards (30d cards stripped per audit). */}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-5">
          <KPICard label="Postings ingested (7d)" value={fmtNum(calib?.surfaced, isLoading)} />
          <KPICard label="Applications (7d)" value={fmtNum(calib?.applied, isLoading)} />
          <KPICard
            label="Response rate"
            value={fmtPct(appliedCount > 0 ? recruiterPlusCount / appliedCount : null, isLoading)}
            caption="screens / applied"
          />
          <KPICard label="Avg time to 1st response" value="—" caption="not computed yet" />
          <KPICard
            label="Offer rate"
            value={fmtPct(appliedCount > 0 ? offerCount / appliedCount : null, isLoading)}
            caption="offers / applied"
          />
        </div>

        {/* Outcome funnel */}
        <OutcomeFunnel rows={funnelRows} />
      </div>
    </AppShell>
  );
}

function fmtNum(n: number | undefined, loading: boolean): string {
  if (loading || n === undefined) return '—';
  return n.toLocaleString();
}

function fmtPct(rate: number | null, loading: boolean): string {
  if (loading || rate === null) return '—';
  return `${Math.round(rate * 100)}%`;
}
