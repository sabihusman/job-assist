import { cn } from '@/lib/utils';

/**
 * 6-row outcome funnel. The backend's `/stats/funnel` covers only the
 * surfaced→interested→applied stages — the deeper interview stages
 * (recruiter screen, phone, video, onsite, offer) come from
 * `/outcomes` rows bucketed in the caller.
 *
 * Each row renders:
 *   [stage label] [bar with count embedded] [percent-of-applied] [↓ drop-off]
 *
 * Bar width is proportional to the row count vs. the Applied row.
 * The drop-off label is computed against the previous row's count
 * (first row has no drop-off).
 */

export type FunnelRow = {
  stage: string;
  count: number;
};

export function OutcomeFunnel({ rows }: { rows: readonly FunnelRow[] }) {
  if (rows.length === 0) return null;
  // Early-return above guarantees rows[0] exists; ``?.count ?? 0`` keeps
  // TS happy without the noNonNullAssertion-flagged ``!``. The ``|| 1``
  // below also avoids divide-by-zero on bar width when count is 0.
  const topCount = (rows[0]?.count ?? 0) || 1;
  return (
    <section className="rounded-md border border-border bg-card p-4">
      <h3 className="mb-3 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        Outcome funnel
      </h3>
      <ol className="flex list-none flex-col gap-2.5 p-0">
        {rows.map((row, i) => {
          const pct = topCount === 0 ? 0 : Math.round((row.count / topCount) * 100);
          // ``i === 0`` short-circuit means rows[i-1] is always defined
          // below; ``?.count ?? 0`` keeps the noNonNullAssertion rule
          // happy without the early-return guard duplicated here.
          const prevCount = rows[i - 1]?.count ?? 0;
          const drop =
            i === 0 ? null : prevCount === 0 ? null : Math.round((1 - row.count / prevCount) * 100);
          return (
            <li key={row.stage} className="flex items-center gap-3 text-[13px]">
              <span className="w-32 shrink-0 text-muted-foreground">{row.stage}</span>
              <div className="relative h-7 flex-1 overflow-hidden rounded bg-surface-2">
                <div
                  className={cn(
                    'h-full bg-primary/30 transition-[width]',
                    row.count === 0 && 'bg-transparent',
                  )}
                  style={{ width: `${pct}%` }}
                />
                <span className="absolute left-2 top-1/2 -translate-y-1/2 font-mono text-[12px] text-foreground/90">
                  {row.count}
                </span>
              </div>
              <span className="w-12 shrink-0 text-right font-mono text-[12px] text-muted-foreground">
                {pct}%
              </span>
              <span className="w-16 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
                {drop !== null && i !== rows.length - 1 && drop > 0 ? `↓${drop}%` : ''}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
