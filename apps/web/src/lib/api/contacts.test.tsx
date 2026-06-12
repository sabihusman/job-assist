import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import {
  toContactUpdateBody,
  toOutreachCreateBody,
  useContactArchive,
  useContactCreate,
  useContactUnarchive,
  useContactUpdate,
  useOutreachLog,
} from '@/lib/api/contacts';
import type { ContactCreate, ContactUpdate, OutreachMessageCreate } from '@/lib/contacts/types';

/**
 * Wire-shape contract tests for PR #52 contact CRUD + outreach
 * mutations. Each test pins the EXACT outgoing request shape so
 * silent rename / typo regressions fail CI before they ship.
 *
 * Following the PR #58 ``useRecordAction`` pattern: mock
 * ``@/lib/api/client`` and assert on the literal arguments
 * openapi-fetch is handed. No legacy-name "absent" assertions —
 * these endpoints are new in PR #52; no pre-existing footgun to
 * guard against.
 */

const { postMock, patchMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
  patchMock: vi.fn(),
}));

vi.mock('@/lib/api/client', () => ({
  api: {
    POST: postMock,
    PATCH: patchMock,
    GET: vi.fn(),
  },
  // feat/view-exports: contactsExportHref builds on the base URL.
  API_BASE_URL: 'http://api.test',
}));

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  postMock.mockReset();
  patchMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── toContactUpdateBody — pure serializer ──────────────────────────────────

describe('toContactUpdateBody', () => {
  test('canonical snake_case names pass through unchanged', () => {
    const patch: ContactUpdate = {
      notes: 'updated',
      contact_opt_in: true,
      target_company_id: '00000000-0000-0000-0000-000000000001',
    };
    expect(toContactUpdateBody(patch)).toEqual({
      notes: 'updated',
      contact_opt_in: true,
      target_company_id: '00000000-0000-0000-0000-000000000001',
    });
  });

  test('undefined fields are stripped from the body', () => {
    // Locking the contract: ``undefined`` (key absent) → don't send.
    // ``null`` (explicit clear) → send. FastAPI's ``exclude_unset=True``
    // semantics depend on this distinction.
    const patch: ContactUpdate = {
      notes: 'kept',
      phone: undefined,
      current_employer: null,
    };
    const body = toContactUpdateBody(patch);
    expect(body).toHaveProperty('notes', 'kept');
    expect(body).toHaveProperty('current_employer', null);
    expect(body).not.toHaveProperty('phone');
  });
});

// ── toOutreachCreateBody — pure serializer ──────────────────────────────────

describe('toOutreachCreateBody', () => {
  test('emits canonical wire shape with all 3 required fields', () => {
    const msg: OutreachMessageCreate = {
      direction: 'outbound',
      channel: 'linkedin',
      sent_at: '2026-06-03T12:00:00Z',
    };
    expect(toOutreachCreateBody(msg)).toEqual({
      direction: 'outbound',
      channel: 'linkedin',
      sent_at: '2026-06-03T12:00:00Z',
    });
  });

  test('source is NEVER emitted (server forces manual)', () => {
    // The hotfix-class footgun: if the serializer ever started
    // forwarding ``source`` from the body, FastAPI's
    // ``extra='forbid'`` on OutreachMessageCreate would 422. Lock
    // it here so the contract can't drift.
    const body = toOutreachCreateBody({
      direction: 'outbound',
      channel: 'email',
      sent_at: '2026-06-03T12:00:00Z',
    });
    expect(body).not.toHaveProperty('source');
    expect(body).not.toHaveProperty('external_message_id');
  });

  test('optional fields included only when present', () => {
    const body = toOutreachCreateBody({
      direction: 'inbound',
      channel: 'email',
      sent_at: '2026-06-03T12:00:00Z',
      subject: 'Re: your note',
      body: 'Thanks!',
      posting_id: 'p-1',
      metadata: { gmail_thread_id: 'thr-xyz' },
    });
    expect(body).toEqual({
      direction: 'inbound',
      channel: 'email',
      sent_at: '2026-06-03T12:00:00Z',
      subject: 'Re: your note',
      body: 'Thanks!',
      posting_id: 'p-1',
      metadata: { gmail_thread_id: 'thr-xyz' },
    });
  });

  test('omits optional keys not present on input', () => {
    const body = toOutreachCreateBody({
      direction: 'outbound',
      channel: 'linkedin',
      sent_at: '2026-06-03T12:00:00Z',
    });
    expect(body).not.toHaveProperty('subject');
    expect(body).not.toHaveProperty('body');
    expect(body).not.toHaveProperty('posting_id');
    expect(body).not.toHaveProperty('metadata');
  });
});

// ── useContactCreate ────────────────────────────────────────────────────────

describe('useContactCreate', () => {
  test('POST body carries every input field; URL has no contact id', async () => {
    const created: ContactCreate = {
      first_name: 'Test',
      last_name: 'Person',
      source_type: 'linkedin_outreach',
      email_primary: 'test@example.test',
    };
    postMock.mockResolvedValue({
      data: { id: 'new', ...created },
      error: null,
      response: new Response(null, { status: 201 }),
    });

    const { result } = renderHook(() => useContactCreate(), { wrapper: wrap() });
    act(() => {
      result.current.mutate(created);
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(postMock).toHaveBeenCalledTimes(1);
    const [path, opts] = postMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
    expect(path).toBe('/contacts');
    expect(opts.body).toMatchObject({
      first_name: 'Test',
      last_name: 'Person',
      source_type: 'linkedin_outreach',
      email_primary: 'test@example.test',
    });
    expect(opts.body).not.toHaveProperty('id');
    expect(opts.body).not.toHaveProperty('created_at');
  });
});

// ── useContactUpdate ────────────────────────────────────────────────────────

describe('useContactUpdate', () => {
  test('id lives in the URL path, NOT in the body', async () => {
    patchMock.mockResolvedValue({
      data: { id: 'c-1', notes: 'updated' },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useContactUpdate(), { wrapper: wrap() });
    act(() => {
      result.current.mutate({ contactId: 'c-1', patch: { notes: 'updated' } });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(patchMock).toHaveBeenCalledTimes(1);
    const [path, opts] = patchMock.mock.calls[0] as [
      string,
      { params: { path: { contact_id: string } }; body: Record<string, unknown> },
    ];
    expect(path).toBe('/contacts/{contact_id}');
    expect(opts.params.path.contact_id).toBe('c-1');
    // The body MUST NOT carry the id.
    expect(opts.body).not.toHaveProperty('id');
    expect(opts.body).not.toHaveProperty('contact_id');
    expect(opts.body).toEqual({ notes: 'updated' });
  });

  test('undefined patch fields are stripped from the wire body', async () => {
    patchMock.mockResolvedValue({
      data: { id: 'c-1' },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    const { result } = renderHook(() => useContactUpdate(), { wrapper: wrap() });
    act(() => {
      result.current.mutate({
        contactId: 'c-1',
        patch: {
          notes: 'kept',
          phone: undefined,
          current_employer: null,
        },
      });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, opts] = patchMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
    expect(opts.body).toEqual({ notes: 'kept', current_employer: null });
    expect(opts.body).not.toHaveProperty('phone');
  });
});

// ── useContactArchive ───────────────────────────────────────────────────────

describe('useContactArchive', () => {
  test('archive POSTs to /contacts/{id}/archive with empty body', async () => {
    postMock.mockResolvedValue({
      data: undefined,
      error: undefined,
      response: new Response(null, { status: 204 }),
    });

    const { result } = renderHook(() => useContactArchive(), { wrapper: wrap() });
    act(() => {
      result.current.mutate('c-1');
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(postMock).toHaveBeenCalledTimes(1);
    const [path, opts] = postMock.mock.calls[0] as [
      string,
      { params: { path: { contact_id: string } }; body?: Record<string, unknown> },
    ];
    expect(path).toBe('/contacts/{contact_id}/archive');
    expect(opts.params.path.contact_id).toBe('c-1');
    // No body — archive takes no data.
    expect(opts.body).toBeUndefined();
  });
});

// ── useContactUnarchive ─────────────────────────────────────────────────────

describe('useContactUnarchive', () => {
  test('unarchive POSTs to /contacts/{id}/unarchive with empty body', async () => {
    postMock.mockResolvedValue({
      data: undefined,
      error: undefined,
      response: new Response(null, { status: 204 }),
    });

    const { result } = renderHook(() => useContactUnarchive(), { wrapper: wrap() });
    act(() => {
      result.current.mutate('c-2');
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [path, opts] = postMock.mock.calls[0] as [
      string,
      { params: { path: { contact_id: string } }; body?: Record<string, unknown> },
    ];
    expect(path).toBe('/contacts/{contact_id}/unarchive');
    expect(opts.params.path.contact_id).toBe('c-2');
    expect(opts.body).toBeUndefined();
  });
});

// ── useOutreachLog ──────────────────────────────────────────────────────────

describe('useOutreachLog', () => {
  test('POST body never carries source, id is in URL path', async () => {
    postMock.mockResolvedValue({
      data: { id: 'm-1', contact_id: 'c-1', source: 'manual' },
      error: null,
      response: new Response(null, { status: 201 }),
    });

    const { result } = renderHook(() => useOutreachLog(), { wrapper: wrap() });
    act(() => {
      result.current.mutate({
        contactId: 'c-1',
        message: {
          direction: 'outbound',
          channel: 'linkedin',
          sent_at: '2026-06-03T12:00:00Z',
          subject: 'Hello',
        },
      });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [path, opts] = postMock.mock.calls[0] as [
      string,
      { params: { path: { contact_id: string } }; body: Record<string, unknown> },
    ];
    expect(path).toBe('/contacts/{contact_id}/outreach');
    expect(opts.params.path.contact_id).toBe('c-1');
    expect(opts.body).toEqual({
      direction: 'outbound',
      channel: 'linkedin',
      sent_at: '2026-06-03T12:00:00Z',
      subject: 'Hello',
    });
    // The contract lock: server forces ``source`` so it must never
    // appear in the request body.
    expect(opts.body).not.toHaveProperty('source');
    expect(opts.body).not.toHaveProperty('external_message_id');
    // contact_id lives in the path, not the body.
    expect(opts.body).not.toHaveProperty('contact_id');
  });

  test('surfaces FastAPI detail on the thrown error', async () => {
    postMock.mockResolvedValue({
      data: null,
      error: { detail: 'reason_required' },
      response: new Response(null, { status: 422 }),
    });

    const { result } = renderHook(() => useOutreachLog(), { wrapper: wrap() });
    act(() => {
      result.current.mutate({
        contactId: 'c-1',
        message: {
          direction: 'outbound',
          channel: 'linkedin',
          sent_at: '2026-06-03T12:00:00Z',
        },
      });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));

    const err = result.current.error as unknown as {
      name: string;
      detail: string | null;
      status: number | null;
    };
    expect(err.name).toBe('MutationError');
    expect(err.detail).toBe('reason_required');
    expect(err.status).toBe(422);
  });
});

// ── feat/view-exports: contactsExportHref ───────────────────────────────────

describe('contactsExportHref', () => {
  test('serializes the same filter set useContacts sends, onto export.csv', async () => {
    const { contactsExportHref } = await import('@/lib/api/contacts');
    const href = contactsExportHref({
      source_type: ['tippie_alumni', 'warm_intro'],
      search: '  jane  ',
      employer: 'Acme',
      include_archived: true,
      limit: 100,
      offset: 0,
    });
    const url = new URL(href, 'http://test');
    expect(url.pathname.endsWith('/contacts/export.csv')).toBe(true);
    expect(url.searchParams.getAll('source_type')).toEqual(['tippie_alumni', 'warm_intro']);
    expect(url.searchParams.get('search')).toBe('jane'); // trimmed like the list query
    expect(url.searchParams.get('employer')).toBe('Acme');
    expect(url.searchParams.get('include_archived')).toBe('true');
    // No pagination params — the export is the full filtered set.
    expect(url.searchParams.has('limit')).toBe(false);
    expect(url.searchParams.has('offset')).toBe(false);
  });

  test('default filters → bare endpoint (no empty params)', async () => {
    const { contactsExportHref } = await import('@/lib/api/contacts');
    const href = contactsExportHref({
      source_type: [],
      search: '',
      employer: '',
      include_archived: false,
      limit: 100,
      offset: 0,
    });
    expect(href.endsWith('/contacts/export.csv')).toBe(true);
  });
});
