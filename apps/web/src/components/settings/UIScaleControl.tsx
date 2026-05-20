'use client';

import { useCallback, useEffect, useState } from 'react';

import { cn } from '@/lib/utils';

/**
 * UI scale controller for Settings → Appearance.
 *
 * Three sub-controls plus a slider:
 *   −  decrement (clamped at 0)
 *   readout  e.g. "+0%", "+4%"
 *   +  increment (clamped at 20)
 *   reset  back to 0
 *   range slider with 0 / +10 / +20 ticks
 *
 * Range 0–20 in 2% steps. The scale persists across sessions in
 * localStorage and is applied to the document root as `font-size:
 * (100+N)%` so every rem-based unit scales together.
 */

const STORAGE_KEY = 'ui-scale-pct';
const MIN = 0;
const MAX = 20;
const STEP = 2;

function applyScale(percent: number) {
  if (typeof document === 'undefined') return;
  // 100% is the browser default; +N% bumps everything proportionally.
  document.documentElement.style.fontSize = `${100 + percent}%`;
}

function readStored(): number {
  if (typeof window === 'undefined') return 0;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === null) return 0;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return 0;
  return Math.min(MAX, Math.max(MIN, parsed));
}

export function UIScaleControl() {
  // Mount with 0 so server-rendered HTML matches; rehydrate from
  // localStorage on the client. Same pattern as ThemeToggle.
  const [percent, setPercent] = useState<number>(0);
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setPercent(readStored());
    setMounted(true);
  }, []);

  // Apply + persist whenever the value changes (post-mount).
  useEffect(() => {
    if (!mounted) return;
    applyScale(percent);
    window.localStorage.setItem(STORAGE_KEY, String(percent));
  }, [percent, mounted]);

  const clamp = (n: number) => Math.min(MAX, Math.max(MIN, n));
  const decrement = useCallback(() => setPercent((p) => clamp(p - STEP)), []);
  const increment = useCallback(() => setPercent((p) => clamp(p + STEP)), []);
  const reset = useCallback(() => setPercent(0), []);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={decrement}
          aria-label="Decrease UI scale"
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border bg-surface text-foreground/80 hover:bg-accent disabled:opacity-40"
          disabled={percent <= MIN}
        >
          −
        </button>
        <span aria-live="polite" className="w-12 text-center font-mono text-[12px] text-foreground">
          {percent >= 0 ? `+${percent}%` : `${percent}%`}
        </span>
        <button
          type="button"
          onClick={increment}
          aria-label="Increase UI scale"
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border bg-surface text-foreground/80 hover:bg-accent disabled:opacity-40"
          disabled={percent >= MAX}
        >
          +
        </button>
        <button
          type="button"
          onClick={reset}
          className="inline-flex h-7 items-center rounded px-2 text-[12px] text-muted-foreground hover:text-foreground"
        >
          reset
        </button>
      </div>

      <div className="flex flex-col gap-1">
        <input
          type="range"
          min={MIN}
          max={MAX}
          step={STEP}
          value={percent}
          aria-label="UI scale percent"
          onChange={(e) => setPercent(Number.parseInt(e.target.value, 10))}
          className="h-1 w-64 cursor-pointer accent-primary"
        />
        <div className="flex w-64 justify-between font-mono text-[10px] text-muted-foreground">
          <span>0%</span>
          <span>+10%</span>
          <span>+20%</span>
        </div>
      </div>

      <p
        className={cn(
          'text-[12px] text-muted-foreground',
          // Keep the helper-text height stable across SSR / CSR.
          mounted ? '' : 'opacity-70',
        )}
      >
        Scales all text, spacing, and cards. 2% steps, up to +20%.
      </p>
    </div>
  );
}
