import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';

/**
 * Test helper that wraps a render in a react-query QueryClientProvider.
 * Use it for any component whose subtree includes a hook from
 * `lib/api/hooks` — without the provider, useQuery throws.
 *
 * Each call constructs a fresh QueryClient so cache state never leaks
 * across tests.
 */
export function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
