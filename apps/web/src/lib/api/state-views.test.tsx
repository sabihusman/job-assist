import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { usePassedPostings, useRejectedPostings } from '@/lib/api/state-views';

/**
 * Wire-shape contract tests for PR #66 pagination hooks.
 *
 * Pinning two things:
 *   1. The outgoing ``limit`` is exactly 100 — never higher than the
 *      API cap. Bestiary 5.11: previously these hooks shipped
 *      ``limit=500`` which 422'd and silently rendered empty state.
 *   2. ``offset`` propagates from the caller through to the wire.
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

describe('usePassedPostings', () => {
  test('sends limit=100 with offset=0 by default', async () => {
    const { result } = renderHook(() => usePassedPostings(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getMock).toHaveBeenCalledTimes(1);
    const [path, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(path).toBe('/postings');
    expect(opts.params.query).toMatchObject({
      state: ['not_interested'],
      limit: 100,
      offset: 0,
    });
    // Critical: limit MUST NOT be 500 (or any value > 100). Bestiary 5.11.
    expect(opts.params.query.limit).toBe(100);
  });

  test('propagates a non-zero offset to the wire', async () => {
    const { result } = renderHook(() => usePassedPostings(100), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(opts.params.query).toMatchObject({ limit: 100, offset: 100 });
  });

  test('does not fire when enabled=false', async () => {
    renderHook(() => usePassedPostings(0, false), { wrapper: wrap() });
    // Give react-query a tick. With enabled=false the queryFn never runs.
    await new Promise((r) => setTimeout(r, 50));
    expect(getMock).not.toHaveBeenCalled();
  });

  test('throws on error so the page-level isError surfaces', async () => {
    getMock.mockResolvedValueOnce({
      data: null,
      error: { detail: 'limit must be 1..100' },
      response: new Response(null, { status: 422 }),
    });
    const { result } = renderHook(() => usePassedPostings(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useRejectedPostings', () => {
  test('sends limit=100 with state=rejected', async () => {
    const { result } = renderHook(() => useRejectedPostings(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [path, opts] = getMock.mock.calls[0] as [
      string,
      { params: { query: Record<string, unknown> } },
    ];
    expect(path).toBe('/postings');
    expect(opts.params.query).toMatchObject({
      state: ['rejected'],
      limit: 100,
      offset: 0,
    });
    expect(opts.params.query.limit).toBe(100);
  });
});
