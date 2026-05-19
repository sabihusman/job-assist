'use client';

import { useTheme } from 'next-themes';
import { useEffect, useState } from 'react';

import { cn } from '@/lib/utils';

/**
 * Light/Dark segmented control. Lives on the Settings page only
 * (per UI_SPEC.md — theme switching is not a global control).
 *
 * `mounted` gate avoids hydration mismatch — next-themes can't know
 * the resolved theme on the server, so the segmented button reads
 * "uncertain" until client-side rehydration completes.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Always treat "light" as the default on first paint to match the
  // server-rendered DOM. Once mounted, read the actual `theme`.
  const current = mounted ? (theme ?? 'light') : 'light';

  return (
    <fieldset aria-label="Theme" className="inline-flex rounded-md border border-border p-0.5">
      {(['light', 'dark'] as const).map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => setTheme(t)}
          data-active={current === t}
          aria-pressed={current === t}
          className={cn(
            'h-7 rounded px-3 text-sm capitalize transition-colors',
            current === t
              ? 'bg-primary text-primary-foreground'
              : 'text-foreground/80 hover:bg-accent',
          )}
        >
          {t}
        </button>
      ))}
    </fieldset>
  );
}
