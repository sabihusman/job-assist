import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { queryKeys, toStateRequestBody, useRecordAction } from '@/lib/api/hooks';
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

// feat/triple-aware-apply (1b): the hook fires a sonner warning toast when an
// applied action returns resume_attached=false. Mock sonner so the contract
// tests can assert exactly when toast.warning is (and isn't) called.
const { warnMock } = vi.hoisted(() => ({ warnMock: vi.fn() }));
vi.mock('sonner', () => ({
  toast: { warning: warnMock, success: vi.fn(), error: vi.fn() },
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
  warnMock.mockReset();
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

  // ── fix(audit): total decrements ONLY for lists containing the posting ─────
  //
  // onMutate used to decrement total on EVERY ['postings', ...] cache entry
  // regardless of membership. The subtitle's applied-count query
  // (state=['applied'], limit=1 — items never contain a triage card) drifted
  // downward on every pass/snooze, and a rapid keyboard burst compounded it
  // before refetches landed.
  test('does NOT decrement totals of cached lists that never contained the posting', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // The triage list contains 'b'; the applied-count entry does not.
    const triageKey = seedListCache(client, [fakePosting('a'), fakePosting('b')]);
    const appliedKey = queryKeys.postings({ state: ['applied'], limit: 1, offset: 0 });
    client.setQueryData<PostingsListResponse>(appliedKey, {
      total: 7,
      offset: 0,
      limit: 1,
      items: [],
    });

    let resolvePost: (v: unknown) => void = () => {};
    postMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = (v) => resolve(v);
      }),
    );

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'b', action_type: 'not_interested' });
    });

    await waitFor(() => {
      // Member list: removed + decremented.
      const triage = client.getQueryData<PostingsListResponse>(triageKey);
      expect(triage?.items.map((p) => p.id)).toEqual(['a']);
      expect(triage?.total).toBe(1);
      // Non-member list: total UNTOUCHED (was 7, stays 7).
      const applied = client.getQueryData<PostingsListResponse>(appliedKey);
      expect(applied?.total).toBe(7);
    });

    resolvePost({ data: { current: 'not_interested' }, error: null });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  test('infinite-list entries decrement by the actual removed count only', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // An infinite cache entry whose pages contain 'x' once; total 200.
    const infKey = queryKeys.postings({});
    client.setQueryData(infKey, {
      pages: [
        { total: 200, offset: 0, limit: 100, items: [fakePosting('x'), fakePosting('y')] },
        { total: 200, offset: 100, limit: 100, items: [fakePosting('z')] },
      ],
      pageParams: [0, 100],
    });
    // A second infinite entry that does NOT contain it.
    const otherKey = queryKeys.postings({ tier: [1] });
    client.setQueryData(otherKey, {
      pages: [{ total: 50, offset: 0, limit: 100, items: [fakePosting('q')] }],
      pageParams: [0],
    });

    let resolvePost: (v: unknown) => void = () => {};
    postMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = (v) => resolve(v);
      }),
    );

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'x', action_type: 'interested' });
    });

    await waitFor(() => {
      const inf = client.getQueryData<{ pages: PostingsListResponse[] }>(infKey);
      expect(inf?.pages[0]?.items.map((p) => p.id)).toEqual(['y']);
      expect(inf?.pages[0]?.total).toBe(199);
      expect(inf?.pages[1]?.total).toBe(199);
      const other = client.getQueryData<{ pages: PostingsListResponse[] }>(otherKey);
      expect(other?.pages[0]?.total).toBe(50); // untouched
    });

    resolvePost({ data: { current: 'interested' }, error: null });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  test('rolls back to the snapshot on POST error', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = seedListCache(client, [fakePosting('a'), fakePosting('b')]);
    postMock.mockResolvedValue({
      data: null,
      error: { detail: 'boom' },
      response: new Response(null, { status: 400 }),
    });

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

  // ── PR #58 wire-shape contract lock ───────────────────────────────────────
  //
  // The Vanta pass-action bug was a wire-body field-name mismatch:
  // production sent ``{kind, reason}``, FastAPI demanded
  // ``{action_type, reason}``. The 422 silently rolled back the
  // optimistic UI to phantom success. This test pins the wire body so
  // the same regression can't reach production again without failing
  // CI first.

  test('POST body always carries action_type (not kind) on the wire', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedListCache(client, [fakePosting('a')]);
    postMock.mockResolvedValue({
      data: {
        current: 'applied',
        reason: null,
        snooze_until: null,
        current_at: new Date().toISOString(),
      },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'a', action_type: 'applied' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // Grab the literal arguments openapi-fetch was handed.
    expect(postMock).toHaveBeenCalledTimes(1);
    const [path, opts] = postMock.mock.calls[0] as [
      string,
      { params: { path: { posting_id: string } }; body: Record<string, unknown> },
    ];
    expect(path).toBe('/postings/{posting_id}/state');
    expect(opts.params.path.posting_id).toBe('a');
    // The contract: action_type present; legacy ``kind`` MUST be absent.
    expect(opts.body).toHaveProperty('action_type', 'applied');
    expect(opts.body).not.toHaveProperty('kind');
    // Nulls for unprovided optionals — keeps the schema happy.
    expect(opts.body).toMatchObject({
      action_type: 'applied',
      reason: null,
      snooze_until: null,
      notes: null,
      // feat/resume-version-tracking: present (null) even when untagged.
      resume_version_id: null,
    });
  });

  // feat/resume-version-tracking: the apply flow passes resume_version_id
  // on the wire when the operator tags a resume in the picker.
  test('POST body carries resume_version_id when an apply is tagged', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedListCache(client, [fakePosting('a')]);
    postMock.mockResolvedValue({
      data: {
        current: 'applied',
        reason: null,
        snooze_until: null,
        current_at: new Date().toISOString(),
      },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({
        postingId: 'a',
        action_type: 'applied',
        resume_version_id: 'rv-123',
      });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, opts] = postMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
    expect(opts.body).toHaveProperty('resume_version_id', 'rv-123');
  });

  // ── feat/triple-aware-apply (1b): resume_attached response contract ────────
  //
  // Sibling to the wire-shape lock above, but for the RESPONSE: the hook must
  // read ``resume_attached`` from the body and surface a non-blocking warning
  // toast ONLY when an applied action returns ``false``. No request-body field
  // is added (the request contract above is unchanged). These pin the
  // read-side contract so a refactor can't silently drop the gap warning.

  const applyResponse = (resume_attached: boolean | null) => ({
    data: {
      current: 'applied',
      reason: null,
      snooze_until: null,
      current_at: new Date().toISOString(),
      resume_attached,
    },
    error: null,
    response: new Response(null, { status: 200 }),
  });

  test('applied with resume_attached=false fires the gap warning toast', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedListCache(client, [fakePosting('a')]);
    postMock.mockResolvedValue(applyResponse(false));

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'a', action_type: 'applied' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(warnMock).toHaveBeenCalledTimes(1);
    expect(warnMock).toHaveBeenCalledWith('Applied — no resume attached');
  });

  test('applied with resume_attached=true does NOT warn', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedListCache(client, [fakePosting('a')]);
    postMock.mockResolvedValue(applyResponse(true));

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'a', action_type: 'applied' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(warnMock).not.toHaveBeenCalled();
  });

  test('non-applied action (resume_attached null) does NOT warn', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedListCache(client, [fakePosting('a')]);
    postMock.mockResolvedValue({
      data: {
        current: 'interested',
        reason: null,
        snooze_until: null,
        current_at: new Date().toISOString(),
        resume_attached: null,
      },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'a', action_type: 'interested' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(warnMock).not.toHaveBeenCalled();
  });

  // ── PR #68 / Bestiary 5.12 cache-collision regression ─────────────────────
  //
  // Before PR #68, ``useSavedFilterCount`` shared the ``['postings', ...]``
  // cache key with ``useTriagePostings`` / ``usePassedPostings`` etc.,
  // but stored a bare ``number`` (the ``.total``) instead of the full
  // ``PostingsListResponse``. ``useRecordAction.onMutate`` iterated every
  // ``['postings', ...]`` entry and crashed on ``prev.items.filter`` for
  // the numeric entries — TypeError thrown synchronously, mutationFn
  // never ran, zero outbound requests.
  //
  // This test seeds BOTH a real list entry AND a numeric saved-filter
  // entry under the same prefix. With the primary fix (distinct key)
  // OR the defense-in-depth shape guard, onMutate must not crash and
  // the POST must fire.

  test('onMutate survives a heterogeneous ["postings", ...] cache (Bestiary 5.12)', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Real list entry — the optimistic remove should still happen here.
    const listKey = seedListCache(client, [fakePosting('a'), fakePosting('b')]);
    // Mimic the bug-shape: a numeric entry under ``['postings', ...]``
    // (pre-PR-#68 ``useSavedFilterCount`` cache shape). The primary fix
    // moves saved-filter counts to ``['postings-count', ...]``, so this
    // entry SHOULDN'T exist under ``['postings', ...]`` in production
    // anymore — but if any future hook lands here, the shape guard
    // protects ``onMutate`` from crashing.
    client.setQueryData(['postings', { limit: 1, offset: 0, state: ['applied'] }], 716);

    postMock.mockResolvedValue({
      data: {
        current: 'not_interested',
        reason: 'wrong_role',
        snooze_until: null,
        current_at: new Date().toISOString(),
      },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({
        postingId: 'a',
        action_type: 'not_interested',
        reason: 'wrong_role',
      });
    });

    // Critical assertion: the mutation reaches the wire. Pre-fix this
    // was zero because onMutate threw TypeError before mutationFn ran.
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(postMock).toHaveBeenCalledTimes(1);

    // The list cache still got the optimistic update.
    const cached = client.getQueryData<PostingsListResponse>(listKey);
    expect(cached?.items.map((p) => p.id)).toEqual(['b']);
    // The numeric entry survives untouched.
    expect(client.getQueryData(['postings', { limit: 1, offset: 0, state: ['applied'] }])).toBe(
      716,
    );
  });

  // ── PR #70 multi-page optimistic remove ───────────────────────────────────
  //
  // After the operator clicks Load More on Triage, the cache holds TWO
  // entries under ``['postings', ...]`` for the same filter set (one
  // per page). Optimistic remove on a card must update BOTH so the
  // card doesn't reappear after the page-2 query refetches.

  test('optimistic remove updates BOTH page-1 and page-2 cache entries (PR #70)', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Page 1 — offset 0
    const page1Key = queryKeys.postings({ limit: 100, offset: 0 });
    client.setQueryData<PostingsListResponse>(page1Key, {
      total: 150,
      offset: 0,
      limit: 100,
      items: [fakePosting('p1-a'), fakePosting('p1-b')],
    });
    // Page 2 — offset 100 (post-Load More)
    const page2Key = queryKeys.postings({ limit: 100, offset: 100 });
    client.setQueryData<PostingsListResponse>(page2Key, {
      total: 150,
      offset: 100,
      limit: 100,
      items: [fakePosting('p2-a'), fakePosting('p2-b')],
    });

    postMock.mockResolvedValue({
      data: {
        current: 'not_interested',
        reason: 'wrong_role',
        snooze_until: null,
        current_at: new Date().toISOString(),
      },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      // Pass a card that lives in page 2 — the optimistic remove must
      // reach across both cache entries to find and drop it.
      result.current.mutate({
        postingId: 'p2-a',
        action_type: 'not_interested',
        reason: 'wrong_role',
      });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const p1 = client.getQueryData<PostingsListResponse>(page1Key);
    const p2 = client.getQueryData<PostingsListResponse>(page2Key);
    // Page 1 untouched in row content (p2-a wasn't there) but total
    // decremented because the loop applies to every cached entry.
    expect(p1?.items.map((p) => p.id)).toEqual(['p1-a', 'p1-b']);
    // Page 2 has p2-a removed.
    expect(p2?.items.map((p) => p.id)).toEqual(['p2-b']);
  });

  test('surfaces the FastAPI detail on the thrown error', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedListCache(client, [fakePosting('a')]);
    postMock.mockResolvedValue({
      data: null,
      error: { detail: 'reason_required_for_not_interested' },
      response: new Response(null, { status: 422 }),
    });

    const { result } = renderHook(() => useRecordAction(), { wrapper: wrap(client) });
    act(() => {
      result.current.mutate({ postingId: 'a', action_type: 'not_interested' });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));

    const err = result.current.error as unknown as {
      name: string;
      kind: string;
      status: number | null;
      detail: string | null;
    };
    expect(err.name).toBe('MutationError');
    expect(err.kind).toBe('application');
    expect(err.status).toBe(422);
    expect(err.detail).toBe('reason_required_for_not_interested');
  });
});

// ── toStateRequestBody — defensive serializer ──────────────────────────────

describe('toStateRequestBody', () => {
  test('canonical RecordActionVars → action_type wire shape', () => {
    expect(toStateRequestBody({ postingId: 'p', action_type: 'applied', reason: null })).toEqual({
      action_type: 'applied',
      reason: null,
      snooze_until: null,
      notes: null,
      resume_version_id: null,
    });
  });

  test('legacy {kind, reason} shape is rewritten to action_type', () => {
    // Belt-and-braces: if some future refactor accidentally hands the
    // hook ``{kind, reason}`` again, the wire body still carries
    // ``action_type`` so the API contract is preserved.
    expect(toStateRequestBody({ kind: 'not_interested', reason: 'wrong_role' })).toEqual({
      action_type: 'not_interested',
      reason: 'wrong_role',
      snooze_until: null,
      notes: null,
      resume_version_id: null,
    });
  });

  test('preserves snooze_until and notes when provided', () => {
    expect(
      toStateRequestBody({
        postingId: 'p',
        action_type: 'snoozed',
        reason: null,
        snooze_until: '2026-06-01T00:00:00Z',
        notes: 'check back in a week',
      }),
    ).toEqual({
      action_type: 'snoozed',
      reason: null,
      snooze_until: '2026-06-01T00:00:00Z',
      notes: 'check back in a week',
      resume_version_id: null,
    });
  });

  test('carries resume_version_id when provided on an applied action', () => {
    expect(
      toStateRequestBody({
        postingId: 'p',
        action_type: 'applied',
        resume_version_id: 'rv-9',
      }),
    ).toEqual({
      action_type: 'applied',
      reason: null,
      snooze_until: null,
      notes: null,
      resume_version_id: 'rv-9',
    });
  });
});
