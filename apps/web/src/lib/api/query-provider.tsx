'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

/**
 * Wraps the app in a single QueryClient instance.
 *
 * The client is held in `useState` so it survives re-renders but is
 * created per-browser-tab (a fresh one in dev's Strict Mode double-mount
 * is harmless — keys hash the same).
 *
 * Defaults err conservative: no auto-refetch on focus, 30s staleness,
 * one retry. Mutations get their own settings per call site.
 */
export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            staleTime: 30_000,
            retry: 1,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
