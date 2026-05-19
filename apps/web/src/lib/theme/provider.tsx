'use client';

import { ThemeProvider as NextThemesProvider } from 'next-themes';
import type { ReactNode } from 'react';

/**
 * next-themes wrapper. `attribute="class"` toggles `<html class="dark">`
 * which our globals.css and Tailwind config key off. Light is the default
 * per UI_SPEC.md ("Light · warm off-white default" inside Settings).
 *
 * `disableTransitionOnChange` avoids a brief Tailwind transition flash
 * when the user toggles theme — purely cosmetic but the Lovable build
 * has the same snap-cut feel.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="light"
      enableSystem={false}
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
