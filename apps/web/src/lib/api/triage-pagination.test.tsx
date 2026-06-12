import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { useTriagePostings, useTriagePostingsInfinite } from '@/lib/api/hooks';
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

// ── fix/audit #5: Load More ACCUMULATES across pages, none dropped ──────────
//
// The old Load More used a single second ``useTriagePostings`` slot keyed on
// a moving offset, so the second click REPLACED the first extra window —
// rows 100–199 silently vanished on the way to 200–299. The infinite query
// flattens every loaded page instead.
describe('useTriagePostingsInfinite (fix/audit #5 accumulation)', () => {
  function pageAt(offset: number, total: number) {
    const items = Array.from({ length: Math.min(100, total - offset) }, (_, i) => ({
      id: `p-${offset + i}`,
    }));
    return { total, offset, limit: 100, items };
  }

  test('two fetchNextPage calls accumulate to 300 rows with no window drop', async () => {
    getMock.mockImplementation(
      async (_path: string, opts: { params: { query: { offset: number } } }) =>
        ok(pageAt(opts.params.query.offset ?? 0, 300)),
    );

    const { result } = renderHook(() => useTriagePostingsInfinite(DEFAULT_FILTERS), {
      wrapper: wrap(),
    });

    // Page 1.
    await waitFor(() => expect(result.current.items).toHaveLength(100));
    expect(result.current.total).toBe(300);
    expect(result.current.hasNextPage).toBe(true);

    // Load More → page 2 appended (rows 0–199, the middle page intact).
    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => expect(result.current.items).toHaveLength(200));

    // Load More → page 3 appended.
    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => expect(result.current.items).toHaveLength(300));

    // Every id from 0..299 is present exactly once — nothing dropped.
    const ids = result.current.items.map((p) => p.id);
    expect(new Set(ids).size).toBe(300);
    expect(ids).toContain('p-150'); // a middle-page row the old code dropped
    expect(result.current.hasNextPage).toBe(false);
  });

  test('omits limit/offset from the cache key (filter still drives invalidation)', async () => {
    getMock.mockResolvedValue(ok(pageAt(0, 5)));
    renderHook(() => useTriagePostingsInfinite(DEFAULT_FILTERS), { wrapper: wrap() });
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    // The first page still requests limit=100/offset=0 on the wire even
    // though they're absent from the key.
    const [path, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(path).toBe('/postings');
    expect(opts.params.query).toMatchObject({ limit: 100, offset: 0 });
  });
});

function ok(data: unknown) {
  return { data, error: null, response: new Response(null, { status: 200 }) };
}
