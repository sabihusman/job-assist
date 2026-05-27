import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { useAllOutcomes, useAppliedPostings } from '@/lib/api/applied';

/**
 * PR #66 pagination tests.
 *
 * ``useAppliedPostings``: same wire-shape contract as state-views —
 * limit MUST be 100, offset propagates, ``enabled=false`` short-circuits.
 *
 * ``useAllOutcomes``: the aggregator. Single ``useQuery`` whose
 * ``queryFn`` loops 100-at-a-time until the cumulative item count
 * reaches the server's reported ``total``. Tests pin:
 *   - Single page (total ≤ 100) → exactly 1 fetch.
 *   - Multi-page (total > 100) → enough fetches to cover total,
 *     with offset advancing per page.
 *   - Short page signal → loop breaks even if total claims more.
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
});

afterEach(() => {
  vi.clearAllMocks();
});

// Helper: build N synthetic outcome rows.
function syntheticOutcomes(n: number, startId = 0) {
  return Array.from({ length: n }, (_, i) => ({
    id: `o-${startId + i}`,
    posting_id: `p-${startId + i}`,
    received_at: new Date().toISOString(),
    outcome_type: 'application_confirmation',
    stage: 'applied',
    notes: null,
  }));
}

describe('useAppliedPostings', () => {
  beforeEach(() => {
    getMock.mockResolvedValue({
      data: { total: 0, offset: 0, limit: 100, items: [] },
      error: null,
      response: new Response(null, { status: 200 }),
    });
  });

  test('sends limit=100 with state=applied', async () => {
    const { result } = renderHook(() => useAppliedPostings(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [path, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(path).toBe('/postings');
    expect(opts.params.query).toMatchObject({
      state: ['applied'],
      limit: 100,
      offset: 0,
    });
    expect(opts.params.query.limit).toBe(100);
  });

  test('propagates offset on Load More', async () => {
    const { result } = renderHook(() => useAppliedPostings(200), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(opts.params.query).toMatchObject({ limit: 100, offset: 200 });
  });
});

describe('useAllOutcomes (aggregator loop)', () => {
  test('total ≤ 100 → exactly one fetch', async () => {
    getMock.mockResolvedValueOnce({
      data: { total: 42, offset: 0, limit: 100, items: syntheticOutcomes(42) },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useAllOutcomes(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(1);
    expect(result.current.data?.items.length).toBe(42);
    expect(result.current.data?.total).toBe(42);
  });

  test('total > 100 → loops with advancing offset until all rows fetched', async () => {
    // 250 total = 3 pages: 100 / 100 / 50.
    getMock
      .mockResolvedValueOnce({
        data: { total: 250, offset: 0, limit: 100, items: syntheticOutcomes(100, 0) },
        error: null,
        response: new Response(null, { status: 200 }),
      })
      .mockResolvedValueOnce({
        data: { total: 250, offset: 100, limit: 100, items: syntheticOutcomes(100, 100) },
        error: null,
        response: new Response(null, { status: 200 }),
      })
      .mockResolvedValueOnce({
        data: { total: 250, offset: 200, limit: 100, items: syntheticOutcomes(50, 200) },
        error: null,
        response: new Response(null, { status: 200 }),
      });

    const { result } = renderHook(() => useAllOutcomes(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(3);
    // Pin the offset progression — first call offset=0, then 100, then 200.
    const offsets = getMock.mock.calls.map(
      (c) => (c[1] as { params: { query: { offset: number } } }).params.query.offset,
    );
    expect(offsets).toEqual([0, 100, 200]);

    // Every call uses limit=100. Bestiary 5.11.
    for (const call of getMock.mock.calls) {
      const limit = (call[1] as { params: { query: { limit: number } } }).params.query.limit;
      expect(limit).toBe(100);
    }

    expect(result.current.data?.items.length).toBe(250);
  });

  test('short page breaks the loop even if total claims more', async () => {
    // total=300 but page 2 returns only 50 items (server-side disagreement
    // or stale total). The loop must stop on the short page rather than
    // looping forever.
    getMock
      .mockResolvedValueOnce({
        data: { total: 300, offset: 0, limit: 100, items: syntheticOutcomes(100, 0) },
        error: null,
        response: new Response(null, { status: 200 }),
      })
      .mockResolvedValueOnce({
        data: { total: 300, offset: 100, limit: 100, items: syntheticOutcomes(50, 100) },
        error: null,
        response: new Response(null, { status: 200 }),
      });

    const { result } = renderHook(() => useAllOutcomes(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(2);
    expect(result.current.data?.items.length).toBe(150);
  });

  test('throws on error so the page-level isError surfaces', async () => {
    getMock.mockResolvedValueOnce({
      data: null,
      error: { detail: 'something broke' },
      response: new Response(null, { status: 500 }),
    });

    const { result } = renderHook(() => useAllOutcomes(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
