'use client';

import { useMemo } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { ExportCsvButton } from '@/components/shared/ExportCsvButton';
import { IngestPanel } from '@/components/stats/IngestPanel';
import { KPICard } from '@/components/stats/KPICard';
import { type FunnelRow, OutcomeFunnel } from '@/components/stats/OutcomeFunnel';
import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';
import { useCalibration } from '@/lib/api/hooks';
import { unifyApplied } from '@/lib/applied/unify';
import { bucketOutcomes } from '@/lib/pipeline/bucket';
import { buildStatsCsv } from '@/lib/stats/exportCsv';

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
 * The funnel's deeper interview stages aren't in `/stats/funnel`, so we
 * compute Applied → recruiter → phone → video → onsite → offer counts
 * client-side from the `/outcomes` dataset (already cached if the user
 * navigated through Applied / Pipeline first) via the same
 * `bucketOutcomes` / `unifyApplied` helpers those pages use — thread-group
 * bucketing rather than filtering on `posting_id` (mostly NULL; see
 * lib/pipeline/bucket.ts's "drop bug" note).
 */
export default function StatsPage() {
  const calibrationQ = useCalibration();
  const appliedQ = useAppliedPostings();
  // job_related=true matches the Pipeline (lib/api/pipeline.ts) and the
  // Applied page (app/applied/page.tsx) — same dataset ``bucketOutcomes``
  // and ``unifyApplied`` are fed elsewhere, so the funnel's counts agree
  // with what those pages show.
  const outcomesQ = useAllOutcomes(true);

  const funnelRows = useMemo<FunnelRow[]>(() => {
    const manualPostings = appliedQ.data?.items ?? [];
    const outcomes = outcomesQ.data?.items ?? [];

    // `outcome_event.posting_id` is NULL for the vast majority of
    // Gmail-derived rows (see lib/applied/types.ts) — only a small
    // linked subset carries it. Filtering on it here used to be "the
    // drop bug" (lib/pipeline/bucket.ts), reading near-zero funnel
    // counts. `bucketOutcomes` groups by email thread instead (no
    // posting_id required) — reuse it, same as the Pipeline board.
    const buckets = bucketOutcomes(outcomes);
    // "Applied" mirrors the Applied page's own count: the unified
    // Gmail + manual fusion (lib/applied/unify.ts), not the narrow
    // manual-only ``useAppliedPostings`` list.
    const appliedEntries = unifyApplied(outcomes, manualPostings);

    return [
      { stage: 'Applied', count: appliedEntries.length },
      { stage: 'Recruiter screen', count: buckets.recruiter.length },
      { stage: 'Phone interview', count: buckets.phone.length },
      { stage: 'Video interview', count: buckets.video.length },
      { stage: 'Onsite', count: buckets.onsite.length },
      { stage: 'Offer', count: buckets.offer.length },
    ];
  }, [appliedQ.data, outcomesQ.data]);

  const calib = calibrationQ.data;
  const appliedCount = funnelRows[0]?.count ?? 0;
  const offerCount = funnelRows[5]?.count ?? 0;
  const recruiterPlusCount = funnelRows.slice(1, 5).reduce((sum, row) => sum + row.count, 0);

  const isLoading = calibrationQ.isLoading || appliedQ.isLoading || outcomesQ.isLoading;

  // Bestiary 5.11 — surface API errors explicitly. Previously this
  // page would silently render zeros / em-dashes when any of the
  // three queries 422'd, making a real data outage indistinguishable
  // from "no data yet."
  const isError = calibrationQ.isError || appliedQ.isError || outcomesQ.isError;
  const errorMsg =
    (calibrationQ.error as Error)?.message ??
    (appliedQ.error as Error)?.message ??
    (outcomesQ.error as Error)?.message ??
    'Unknown error';

  return (
    <AppShell title="Stats" subtitle="Operator metrics">
      <div className="flex flex-col gap-6 px-6 py-4">
        {isError ? (
          <ErrorCard
            message={errorMsg}
            onRetry={() => {
              calibrationQ.refetch();
              appliedQ.refetch();
              outcomesQ.refetch();
            }}
          />
        ) : (
          <>
            {/* feat/view-exports: the page's own client-computed numbers
                (KPIs + funnel) as (section, metric, value) rows — exactly as
                displayed. The ingest panel self-fetches and stays out. */}
            <div className="flex justify-end">
              <ExportCsvButton
                buildCsv={() =>
                  buildStatsCsv(
                    [
                      { metric: 'Postings ingested (7d)', value: fmtNum(calib?.surfaced, false) },
                      { metric: 'Applications (7d)', value: fmtNum(calib?.applied, false) },
                      {
                        metric: 'Response rate',
                        value: fmtPct(
                          appliedCount > 0 ? recruiterPlusCount / appliedCount : null,
                          false,
                        ),
                      },
                      {
                        metric: 'Offer rate',
                        value: fmtPct(appliedCount > 0 ? offerCount / appliedCount : null, false),
                      },
                    ],
                    funnelRows,
                  )
                }
                filenamePrefix="stats-export"
                disabled={isLoading}
                testId="stats-export-button"
                title="Download a .csv of the KPI cards and outcome funnel as currently shown."
              />
            </div>

            {/* KPI grid — 5 cards (30d cards stripped per audit). */}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-5">
              <KPICard label="Postings ingested (7d)" value={fmtNum(calib?.surfaced, isLoading)} />
              <KPICard label="Applications (7d)" value={fmtNum(calib?.applied, isLoading)} />
              <KPICard
                label="Response rate"
                value={fmtPct(
                  appliedCount > 0 ? recruiterPlusCount / appliedCount : null,
                  isLoading,
                )}
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

            {/* Ingest health (feat/ingest-visibility) — self-fetches /stats/ingest. */}
            <IngestPanel />
          </>
        )}
      </div>
    </AppShell>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section
      data-testid="stats-error"
      className="rounded-md border border-negative/40 bg-negative/5 p-4"
    >
      <h2 className="text-sm font-semibold text-negative">Couldn&apos;t load stats.</h2>
      <p className="mt-1 text-[13px] text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
      >
        Retry
      </button>
    </section>
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
