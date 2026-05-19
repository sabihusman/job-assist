import { cn } from '@/lib/utils';

/**
 * Single read-only KPI tile. The big number reads as the metric value
 * (28-32px / 700); the label is uppercase mono muted; an optional
 * inline delta picks up positive/negative tinting from the sign.
 *
 * `value` is rendered verbatim — callers format units (%, k, USD)
 * before passing in.
 */
export function KPICard({
  label,
  value,
  delta,
  caption,
}: {
  label: string;
  value: string;
  delta?: number | null;
  caption?: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border bg-card p-4 shadow-card">
      <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="flex items-baseline gap-2">
        <span className="text-[28px] font-bold tracking-tight">{value}</span>
        {delta != null && (
          <span
            className={cn(
              'font-mono text-[12px]',
              delta > 0 ? 'text-positive' : delta < 0 ? 'text-negative' : 'text-muted-foreground',
            )}
          >
            {delta > 0 ? '+' : ''}
            {delta}
          </span>
        )}
      </div>
      {caption && <span className="text-[11px] text-muted-foreground">{caption}</span>}
    </div>
  );
}
