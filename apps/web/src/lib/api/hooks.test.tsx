import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { queryKeys, useRecordAction } from '@/lib/api/hooks';
import type { PostingsListResponse } from '@/lib/triage/types';

// Replace the openapi-fetch client with an inline mock so the test can
// control whether POST /postings/{id}/state succeeds or fails. Uses
// `vi.hoisted` because vi.mock factories are hoisted above local
// const declarations.
const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock('@/lib/api/client', () => ({
  api: {
    POST: postMock,
    GET: vi.fn(),
  },
}));

function seedListCache(client: QueryClient, items: PostingsListResponse['items']) {
  const key = queryKeys.postings({ limit: 20, offset: 0 });
  client.setQueryData<PostingsListResponse>(key, {
    total: items.length,
    offset: 0,
    limit: 20,
    items,
  });
  return key;
}

function wrap(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const fakePosting = (id: string) =>
  ({
    id,
    company: { id: `c-${id}`, name: id, domain: null, description: null, tier: 1 },
    role: {
      title: 'r',
      family: null,
      department: null,
      team: null,
      seniority: null,
    },
    location_raw: null,
    locations_normalized: [],
    remote_type: null,
    salary: null,
    source: { ats: 'greenhouse', url: null },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  }) satisfies PostingsListResponse['items'][number];

beforeEach(() => {
  postMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useRecordAction', () => {
  test('optimistically removes the posting from a cached list on mutate', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = seedListCache(client, [fakePosting('a'), fakePosting('b'), fakePosting('c')]);

    // Resolve POST after we've checked the optimistic snapshot.
    let resolvePost: (v: unknown) => void = () => {};
    postMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = (v) => resolve(v);
      }),
    );

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'b', action_type: 'interested' });
    });

    // Immediately — before the POST resolves — the cache should reflect
    // the optimistic remove.
    await waitFor(() => {
      const cached = client.getQueryData<PostingsListResponse>(key);
      expect(cached?.items.map((p) => p.id)).toEqual(['a', 'c']);
      expect(cached?.total).toBe(2);
    });

    // Resolve the POST so the test finishes cleanly.
    resolvePost({ data: { current: 'interested' }, error: null });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  test('rolls back to the snapshot on POST error', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = seedListCache(client, [fakePosting('a'), fakePosting('b')]);
    postMock.mockResolvedValue({ data: null, error: { detail: 'boom' } });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'a', action_type: 'applied' });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    // After error, the snapshot is restored — both items reappear.
    const cached = client.getQueryData<PostingsListResponse>(key);
    expect(cached?.items.map((p) => p.id)).toEqual(['a', 'b']);
    expect(cached?.total).toBe(2);
  });
});
