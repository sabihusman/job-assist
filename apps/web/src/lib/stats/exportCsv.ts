import type { FunnelRow } from '@/components/stats/OutcomeFunnel';
import { buildCsv } from '@/lib/csv';

/**
 * CSV of the CURRENT Stats view (feat/view-exports): the KPI cards and the
 * outcome funnel as `(section, metric, value)` rows — the page's own
 * client-computed numbers, exactly as displayed. The ingest panel self-fetches
 * its own data and stays out of this export.
 */

const HEADERS = ['section', 'metric', 'value'] as const;

export type StatsKpi = { metric: string; value: string };

export function buildStatsCsv(kpis: readonly StatsKpi[], funnel: readonly FunnelRow[]): string {
  const rows: Array<readonly [string, string, string | number]> = [
    ...kpis.map((k) => ['kpi', k.metric, k.value] as const),
    ...funnel.map((f) => ['funnel', f.stage, f.count] as const),
  ];
  return buildCsv(HEADERS, rows);
}
