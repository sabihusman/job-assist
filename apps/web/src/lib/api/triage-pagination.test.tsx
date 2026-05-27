import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { useTriagePostings } from '@/lib/api/hooks';
import { DEFAULT_FILTERS } from '@/lib/triage/filters';

/**
 * PR #70 / Bestiary 5.13 — Triage pagination contract tests.
 *
 * Triage shipped with a hardcoded 20-row default that left 96% of
 * postings unreachable once the corpus grew. This pins the new
 * defaults so the regression can't sneak back in:
 *   - DEFAULT_FILTERS.limit === 100 (API cap)
 *   - Offset propagates through to the wire on Load More
 *   - ``enabled=false`` short-circuits (used by the page's second
 *     hook instance until the operator clicks Load More)
 */

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock('@/lib/api/client', () => ({
  api: { GET: getMock, POST: vi.fn(), PATCH: vi.fn() },
}));

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({
    data: { total: 0, offset: 0, limit: 100, items: [] },
    error: null,
    response: new Response(null, { status: 200 }),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useTriagePostings (PR #70 pagination)', () => {
  test('default filters send limit=100 (Bestiary 5.13)', async () => {
    const { result } = renderHook(() => useTriagePostings(DEFAULT_FILTERS), {
      wrapper: wrap(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(1);
    const [path, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(path).toBe('/postings');
    // Critical regression lock: limit MUST be 100, NOT the legacy 20.
    expect(opts.params.query.limit).toBe(100);
    expect(opts.params.query.offset).toBe(0);
  });

  test('propagates non-zero offset on Load More', async () => {
    const { result } = renderHook(() => useTriagePostings({ ...DEFAULT_FILTERS, offset: 100 }), {
      wrapper: wrap(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(opts.params.query).toMatchObject({ limit: 100, offset: 100 });
  });

  test('does not fire when enabled=false (extra-page guard)', async () => {
    renderHook(() => useTriagePostings(DEFAULT_FILTERS, false), { wrapper: wrap() });
    // Give react-query a tick. With enabled=false the queryFn never runs.
    await new Promise((r) => setTimeout(r, 50));
    expect(getMock).not.toHaveBeenCalled();
  });
});
