'use client';

import { useState } from 'react';

import { type HealthSeverity, type IngestHealth, useIngestHealth } from '@/lib/api/health';
import { cn } from '@/lib/utils';

/**
 * System-health traffic-light dot, shown right at the "Job Assist" title
 * (feat/health-indicator). Backed by GET /admin/ingest/health, polled every 60s.
 *
 * Disambiguation from the FOOTER "synced Xs ago" dot (which uses the muted
 * ``bg-positive`` mint and is always green): this dot lives at the TOP next to
 * the title, uses a SATURATED traffic-light palette (emerald / amber / red),
 * and CHANGES colour with system health — so the two never read as the same
 * signal. Hover/click reveals a popover of which checks pass/fail.
 */

type DotState = HealthSeverity | 'loading';

const DOT_CLASS: Record<DotState, string> = {
  ok: 'bg-emerald-500',
  degraded: 'bg-amber-500',
  down: 'bg-red-500',
  loading: 'bg-muted-foreground/40 animate-pulse',
};

const STATE_LABEL: Record<DotState, string> = {
  ok: 'All systems healthy',
  degraded: 'Degraded — needs attention',
  down: 'Down — action required',
  loading: 'Checking…',
};

const CHECK_LABELS: Record<keyof IngestHealth['checks'], string> = {
  recent_success: 'Ingest ran recently',
  no_hard_failures: 'No failed runs',
  broad_fresh: 'Broad-ingest fresh',
  not_starved: 'New roles flowing in',
  llm_healthy: 'LLM (Gemini) healthy',
};

function fmtTime(iso: string | null): string {
  if (!iso) return 'never';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'unknown';
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** Pure presentational dot + popover — tested directly with props. */
export function HealthDotView({
  state,
  health,
  isError,
}: {
  state: DotState;
  health: IngestHealth | undefined;
  isError: boolean;
}) {
  // Two independent triggers that never fight each other: hover shows it
  // transiently; click PINS it open so it survives the mouse leaving. (A single
  // toggle broke because a click also fires mouseenter — hover-open then
  // click-close.)
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovered || pinned;

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        aria-label={`System health: ${STATE_LABEL[state]}`}
        data-testid="health-dot"
        data-state={state}
        onClick={() => setPinned((p) => !p)}
        className={cn(
          'h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-inset ring-black/10 transition-colors',
          DOT_CLASS[state],
        )}
      />

      {open && (
        <div
          data-testid="health-popover"
          className="absolute left-1/2 top-5 z-50 w-60 -translate-x-1/2 rounded-md border border-border bg-popover p-3 text-left shadow-md"
        >
          <div className="flex items-center gap-2">
            <span className={cn('h-2 w-2 rounded-full', DOT_CLASS[state])} />
            <span className="text-[12px] font-semibold">{STATE_LABEL[state]}</span>
          </div>

          {isError ? (
            <p className="mt-2 text-[11px] text-muted-foreground">
              Backend unreachable — the API isn&apos;t responding. Treated as down.
            </p>
          ) : state === 'loading' || !health ? (
            <p className="mt-2 text-[11px] text-muted-foreground">Checking ingest status…</p>
          ) : (
            <>
              <ul className="mt-2 flex list-none flex-col gap-1 p-0">
                {(Object.keys(CHECK_LABELS) as (keyof IngestHealth['checks'])[]).map((key) => {
                  const pass = health.checks[key];
                  return (
                    <li
                      key={key}
                      data-testid={`health-check-${key}`}
                      data-pass={pass}
                      className="flex items-center justify-between text-[11px]"
                    >
                      <span className="text-muted-foreground">{CHECK_LABELS[key]}</span>
                      <span
                        aria-hidden="true"
                        className={cn('font-mono', pass ? 'text-emerald-500' : 'text-red-500')}
                      >
                        {pass ? '✓' : '✗'}
                      </span>
                    </li>
                  );
                })}
              </ul>
              <div className="mt-2 flex flex-col gap-0.5 border-t border-border pt-2 font-mono text-[10px] text-muted-foreground">
                <p>last ingest: {fmtTime(health.metrics.last_success_at)}</p>
                <p>LLM last used: {fmtTime(health.metrics.llm_last_used_at)}</p>
              </div>
            </>
          )}
        </div>
      )}
    </span>
  );
}

/** Container: polls /admin/ingest/health and resolves the effective dot state. */
export function HealthDot() {
  const { data, isError, isLoading } = useIngestHealth();

  // Unreachable backend → DOWN (never green/unknown). First load with no data
  // yet → a neutral pulsing dot. Otherwise the server-computed severity (with a
  // defensive fallback derived from `ok` for an older backend missing it).
  const state: DotState = isError
    ? 'down'
    : isLoading && !data
      ? 'loading'
      : (data?.severity ?? (data?.ok ? 'ok' : 'down'));

  return <HealthDotView state={state} health={data} isError={isError} />;
}
