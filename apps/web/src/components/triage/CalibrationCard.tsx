'use client';

import { ArrowRight, Sparkles } from 'lucide-react';
import Link from 'next/link';

import { useCalibration } from '@/lib/api/hooks';
import { familyLabel } from '@/lib/triage/family-labels';

/**
 * Calibration card under the filter row.
 *
 * Always renders — even on first load before useCalibration resolves —
 * with `—` placeholders. UI_SPEC.md flagged that the source build
 * has no skeleton pattern; the dash-placeholder is the spec's
 * substitute.
 *
 * `interested_rate === null` happens when surfaced is 0 (PR #30b
 * spec). We render `—` rather than crashing on the `* 100` math.
 */
export function CalibrationCard() {
  const { data, isLoading } = useCalibration();

  const surfaced = data?.surfaced ?? null;
  const interested = data?.interested ?? null;
  const interestedRate = data?.interested_rate ?? null;
  const applied = data?.applied ?? null;
  const rejected = data?.rejected_by_you ?? null;
  const topReasons = data?.top_rejected_role_families ?? [];

  return (
    <section className="flex items-start justify-between rounded-md border border-border bg-card p-4">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          This week&apos;s calibration
        </div>

        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
          <Kpi label="Surfaced" value={fmtNum(surfaced, isLoading)} />
          <span aria-hidden="true" className="text-muted-foreground">
            ·
          </span>
          <Kpi
            label="Interested"
            value={fmtNum(interested, isLoading)}
            extra={`(${fmtPct(interestedRate, isLoading)})`}
            valueClassName="text-positive"
          />
          <span aria-hidden="true" className="text-muted-foreground">
            ·
          </span>
          <Kpi label="Applied" value={fmtNum(applied, isLoading)} valueClassName="text-primary" />
          <span aria-hidden="true" className="text-muted-foreground">
            ·
          </span>
          <Kpi
            label="Rejected by you"
            value={fmtNum(rejected, isLoading)}
            valueClassName="text-negative"
          />
        </div>

        <p className="flex flex-wrap items-center gap-1.5 text-[13px] text-muted-foreground">
          <span>Top &quot;wrong&quot; reasons:</span>
          {topReasons.length === 0 ? (
            <span>—</span>
          ) : (
            topReasons.map((r, i) => (
              <span key={r.role_family} className="flex items-center gap-1">
                {i > 0 && (
                  <span aria-hidden="true" className="text-muted-foreground/60">
                    ,
                  </span>
                )}
                <span className="rounded bg-surface-2 px-1.5 py-0.5 text-foreground/80">
                  {familyLabel(r.role_family)}
                </span>
                <span className="font-mono text-[11px]">({r.count})</span>
              </span>
            ))
          )}
        </p>
      </div>

      <Link
        href="/settings"
        className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-surface px-3 text-sm text-foreground/80 hover:bg-accent"
      >
        Tune surfacing
        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      </Link>
    </section>
  );
}

function Kpi({
  label,
  value,
  extra,
  valueClassName,
}: {
  label: string;
  value: string;
  extra?: string;
  valueClassName?: string;
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className={`font-semibold ${valueClassName ?? 'text-foreground'}`}>{value}</span>
      {extra && <span className="font-mono text-[12px] text-muted-foreground">{extra}</span>}
    </span>
  );
}

function fmtNum(n: number | null, loading: boolean): string {
  if (loading || n === null) return '—';
  return String(n);
}

function fmtPct(rate: number | null, loading: boolean): string {
  if (loading || rate === null) return '—';
  return `${Math.round(rate * 100)}%`;
}
