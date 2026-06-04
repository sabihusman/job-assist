'use client';

import { KPICard } from '@/components/stats/KPICard';
import { type IngestStats, useIngestStats } from '@/lib/api/ingest';
import { cn } from '@/lib/utils';

/**
 * Ingest-health panel (feat/ingest-visibility). Surfaces the previously
 * write-only `ingest_run` audit table so the operator can glance and see
 * whether the daily cron is actually landing postings: new-posting totals,
 * per-source last status (green/red), and a daily new-posting bar list.
 */
export function IngestPanel() {
  const { data, isLoading, isError } = useIngestStats(14);

  if (isError) {
    return (
      <Section>
        <p className="text-[13px] text-muted-foreground">Couldn&apos;t load ingest health.</p>
      </Section>
    );
  }

  const totals = data?.totals;
  const daily = data?.daily ?? [];
  const bySource = data?.by_source ?? [];
  const maxNew = Math.max(1, ...daily.map((d) => d.postings_new));

  return (
    <Section>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <KPICard label="New postings (14d)" value={fmtNum(totals?.postings_new, isLoading)} />
        <KPICard
          label="Ingest runs (14d)"
          value={fmtNum(totals?.runs, isLoading)}
          caption={
            totals ? `${totals.successes} ok · ${totals.failures} failed` : 'success / failed'
          }
        />
        <KPICard
          label="Sources active"
          value={fmtNum(bySource.length, isLoading)}
          caption="with a recorded run"
        />
      </div>

      {/* Per-source last run — the green/red "is it working" glance. */}
      {bySource.length > 0 && (
        <div className="mt-4 flex flex-col gap-1.5" data-testid="ingest-by-source">
          {bySource.map((s) => (
            <div
              key={s.source}
              className="flex items-center justify-between rounded-md border border-border bg-card px-3 py-2 text-[13px]"
            >
              <span className="flex items-center gap-2">
                <StatusDot status={s.status} />
                <span className="font-medium">{s.source}</span>
                <span className="text-muted-foreground">{s.status}</span>
              </span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {fmtDate(s.last_run_at)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Daily new-posting bars — most recent first. */}
      {daily.length > 0 && (
        <div className="mt-4" data-testid="ingest-daily">
          <h4 className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
            New postings / day
          </h4>
          <ul className="mt-2 flex list-none flex-col gap-1 p-0">
            {daily.map((d) => (
              <li key={d.day} className="flex items-center gap-2 text-[12px]">
                <span className="w-20 shrink-0 font-mono text-[11px] text-muted-foreground">
                  {fmtDate(d.day)}
                </span>
                <span
                  className={cn(
                    'h-3 rounded-sm',
                    d.failures > 0 ? 'bg-negative/60' : 'bg-positive/60',
                  )}
                  style={{ width: `${Math.round((d.postings_new / maxNew) * 160) + 2}px` }}
                  aria-hidden="true"
                />
                <span className="font-mono tabular-nums">{d.postings_new}</span>
                {d.failures > 0 && (
                  <span className="font-mono text-[11px] text-negative">{d.failures} failed</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!isLoading && daily.length === 0 && bySource.length === 0 && (
        <p className="mt-3 text-[13px] text-muted-foreground">
          No ingest runs recorded in the last 14 days.
        </p>
      )}
    </Section>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-border bg-surface p-4" aria-label="Ingest health">
      <h3 className="mb-3 text-sm font-semibold">Ingest health</h3>
      {children}
    </section>
  );
}

function StatusDot({ status }: { status: string }) {
  const tone =
    status === 'success' ? 'bg-positive' : status === 'failed' ? 'bg-negative' : 'bg-pending'; // running / partial / handle_not_found
  return <span aria-hidden="true" className={cn('h-2 w-2 rounded-full', tone)} />;
}

function fmtNum(n: number | undefined, loading: boolean): string {
  if (loading) return '…';
  return n == null ? '—' : String(n);
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export type { IngestStats };
