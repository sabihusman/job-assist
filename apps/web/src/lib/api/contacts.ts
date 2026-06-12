'use client';

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import { MutationError, extractDetail } from '@/lib/api/mutation-error';
import type {
  ContactCreate,
  ContactDetail,
  ContactUpdate,
  ContactsFilters,
  ContactsListResponse,
  OutreachMessage,
  OutreachMessageCreate,
  OutreachMessageListResponse,
} from '@/lib/contacts/types';

/**
 * Hook for the outreach contacts list (PR #51).
 *
 * Lives in its own module — the contacts page doesn't ship with the
 * Triage / Applied bundles, and pulling the unrelated symbols would
 * inflate them.
 */

const CONTACTS_KEY = 'contacts' as const;
const OUTREACH_KEY = 'outreach' as const;

export const contactsKeys = {
  list: (params: Record<string, unknown> = {}) => [CONTACTS_KEY, params] as const,
  detail: (id: string) => [CONTACTS_KEY, 'detail', id] as const,
};

export const outreachKeys = {
  forContact: (contactId: string, params: Record<string, unknown> = {}) =>
    [OUTREACH_KEY, contactId, params] as const,
};

// Per-page size for the contacts list. 100 = the backend's max ``limit``
// (main.py enforces 1..100), so the operator reaches all 374 alumni in at
// most ceil(374/100)=4 pages of Load More instead of being stuck at 50.
const CONTACTS_PAGE_SIZE = 100;

/**
 * Filter params WITHOUT limit/offset — those vary per page and must NOT be
 * part of the cache key, or every page would key to a different query.
 */
function toFilterQuery(filters: ContactsFilters): Record<string, unknown> {
  const q: Record<string, unknown> = {};
  if (filters.source_type.length) q.source_type = filters.source_type;
  if (filters.search.trim()) q.search = filters.search.trim();
  // feat/warm-path-badge: company filter from the badge click-through.
  if (filters.employer.trim()) q.employer = filters.employer.trim();
  if (filters.include_archived) q.include_archived = true;
  return q;
}

/**
 * Paginated contacts list (fix/contacts-pagination).
 *
 * Previously a single ``useQuery`` capped at limit=50, with no Load More —
 * so 324 of 374 seeded alumni were unreachable (and the backend caps limit
 * at 100, so a single fetch can never return all 374). This is now an
 * ``useInfiniteQuery`` that pages by offset; the page renders the flattened
 * accumulation plus a Load More button driven by ``hasNextPage``.
 *
 * Returns the raw infinite-query object plus convenience ``items`` (flattened
 * across loaded pages) and ``total`` (server-reported full count).
 */
export function useContacts(filters: ContactsFilters) {
  const filterQuery = toFilterQuery(filters);
  const query = useInfiniteQuery({
    queryKey: contactsKeys.list(filterQuery),
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/contacts', {
        params: {
          query: {
            ...filterQuery,
            limit: CONTACTS_PAGE_SIZE,
            offset: pageParam,
          } as never,
        },
      });
      if (error) throw error;
      return data as unknown as ContactsListResponse;
    },
    // Next offset = how many we've loaded so far; undefined once we've
    // loaded ``total`` rows (Load More then disables).
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, page) => n + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const total = query.data?.pages[0]?.total ?? 0;
  return { ...query, items, total };
}

// Per-page size for the infinite outreach timeline (matches the backend's
// default ``limit``).
const OUTREACH_PAGE_SIZE = 50;

/**
 * Per-contact outreach timeline as an accumulating ``useInfiniteQuery``
 * (fix/audit #5). The OutreachTimeline previously layered a single extra
 * page on top of the parent's first page via a moving ``offset`` — so a
 * second Load More REPLACED the extra window and dropped the page between.
 * This pages by offset and flattens, matching ``useContacts``.
 *
 * Returns the raw infinite-query object plus convenience ``items``
 * (flattened) and ``total`` (server-reported full count).
 */
export function useContactOutreachInfinite(contactId: string | null) {
  const query = useInfiniteQuery({
    queryKey: contactId
      ? outreachKeys.forContact(contactId, { infinite: true })
      : [OUTREACH_KEY, '__none__', { infinite: true }],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      if (!contactId) throw new Error('useContactOutreachInfinite called without id');
      const { data, error } = await api.GET('/contacts/{contact_id}/outreach', {
        params: {
          path: { contact_id: contactId },
          query: { limit: OUTREACH_PAGE_SIZE, offset: pageParam } as never,
        },
      });
      if (error) throw error;
      return data as unknown as OutreachMessageListResponse;
    },
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, page) => n + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    enabled: !!contactId,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const total = query.data?.pages[0]?.total ?? 0;
  return { ...query, items, total };
}

// ── PR #52 — detail query + CRUD + outreach mutations ──────────────────────

/** Single contact detail. */
export function useContactDetail(contactId: string | null) {
  return useQuery({
    queryKey: contactId ? contactsKeys.detail(contactId) : [CONTACTS_KEY, 'detail', '__none__'],
    queryFn: async () => {
      if (!contactId) throw new Error('useContactDetail called without id');
      const { data, error } = await api.GET('/contacts/{contact_id}', {
        params: { path: { contact_id: contactId } },
      });
      if (error) throw error;
      return data as unknown as ContactDetail;
    },
    enabled: !!contactId,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

/** Per-contact outreach timeline. */
export function useContactOutreach(
  contactId: string | null,
  params: { limit?: number; offset?: number } = {},
) {
  const query: Record<string, unknown> = {
    limit: params.limit ?? 50,
    offset: params.offset ?? 0,
  };
  return useQuery({
    queryKey: contactId
      ? outreachKeys.forContact(contactId, query)
      : [OUTREACH_KEY, '__none__', query],
    queryFn: async () => {
      if (!contactId) throw new Error('useContactOutreach called without id');
      const { data, error } = await api.GET('/contacts/{contact_id}/outreach', {
        params: {
          path: { contact_id: contactId },
          query: query as never,
        },
      });
      if (error) throw error;
      return data as unknown as OutreachMessageListResponse;
    },
    enabled: !!contactId,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}

// ── Wire-body serializers (PR #58 pattern) ─────────────────────────────────
//
// Each mutation has an explicit serializer that produces the EXACT
// wire shape the API expects. Unit tests in ``contacts.test.tsx``
// lock these contracts:
//   - canonical snake_case names present
//   - undefined / unset fields omitted (don't send them at all)
//   - server-set fields (``source`` on outreach) absent
// No legacy-name guard — these endpoints are new in PR #52; no
// pre-existing footgun to defend against.

/**
 * Strip ``undefined`` keys but PRESERVE ``null`` (which is the
 * "explicit clear" signal — see ``ContactUpdate`` in
 * ``lib/contacts/types.ts``). ``JSON.stringify`` drops ``undefined``
 * naturally but we strip up-front so the wire body, contract test,
 * and openapi-fetch agree.
 */
function pruneUndefined<T extends Record<string, unknown>>(obj: T): Partial<T> {
  const out: Partial<T> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) (out as Record<string, unknown>)[k] = v;
  }
  return out;
}

export function toContactUpdateBody(vars: ContactUpdate): Partial<ContactUpdate> {
  return pruneUndefined(vars);
}

export function toOutreachCreateBody(vars: OutreachMessageCreate): Record<string, unknown> {
  // Explicit allow-list: server forces ``source`` so it must NEVER
  // appear in the wire body. ``external_message_id`` is also
  // forbidden — PR #53's gmail_auto write path bypasses this hook.
  const body: Record<string, unknown> = {
    direction: vars.direction,
    channel: vars.channel,
    sent_at: vars.sent_at,
  };
  if (vars.subject !== undefined) body.subject = vars.subject;
  if (vars.body !== undefined) body.body = vars.body;
  if (vars.posting_id !== undefined) body.posting_id = vars.posting_id;
  if (vars.metadata !== undefined) body.metadata = vars.metadata;
  return body;
}

// ── Mutation factory: standardize the MutationError throw ──────────────────

type OpenapiResult<T> = {
  data?: T | undefined;
  error?: unknown;
  response?: Response | undefined;
};

function throwIfError<T>(result: OpenapiResult<T>, fallbackLabel: string): T {
  if (result.error || result.data === undefined) {
    throw new MutationError({
      kind: 'application',
      status: result.response?.status ?? null,
      detail: extractDetail(result.error),
      message:
        extractDetail(result.error) ??
        `${fallbackLabel} (${result.response?.status ?? 'no status'})`,
    });
  }
  return result.data;
}

// ── useContactCreate ─────────────────────────────────────────────────────────

export function useContactCreate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: ContactCreate) => {
      const result = (await api.POST('/contacts', {
        body: pruneUndefined(vars as Record<string, unknown>) as never,
      })) as OpenapiResult<ContactDetail>;
      return throwIfError(result, 'Create failed');
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [CONTACTS_KEY] });
    },
  });
}

// ── useContactUpdate ─────────────────────────────────────────────────────────

export type ContactUpdateVars = { contactId: string; patch: ContactUpdate };

export function useContactUpdate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: ContactUpdateVars) => {
      const result = (await api.PATCH('/contacts/{contact_id}', {
        params: { path: { contact_id: vars.contactId } },
        body: toContactUpdateBody(vars.patch) as never,
      })) as OpenapiResult<ContactDetail>;
      return throwIfError(result, 'Update failed');
    },
    onSuccess: (data, vars) => {
      // Refresh the detail query for this contact + the surrounding
      // list (current_employer / current_position might have changed,
      // which would change a row's rendering).
      qc.setQueryData(contactsKeys.detail(vars.contactId), data);
      qc.invalidateQueries({ queryKey: [CONTACTS_KEY] });
    },
  });
}

// ── useContactArchive / useContactUnarchive ─────────────────────────────────

export function useContactArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (contactId: string) => {
      const result = (await api.POST('/contacts/{contact_id}/archive', {
        params: { path: { contact_id: contactId } },
      })) as OpenapiResult<unknown>;
      // 204 No Content — openapi-fetch returns data === undefined but
      // also error === undefined. Treat as success.
      if (result.error) {
        throw new MutationError({
          kind: 'application',
          status: result.response?.status ?? null,
          detail: extractDetail(result.error),
          message:
            extractDetail(result.error) ??
            `Archive failed (${result.response?.status ?? 'no status'})`,
        });
      }
      return null;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [CONTACTS_KEY] });
    },
  });
}

export function useContactUnarchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (contactId: string) => {
      const result = (await api.POST('/contacts/{contact_id}/unarchive', {
        params: { path: { contact_id: contactId } },
      })) as OpenapiResult<unknown>;
      if (result.error) {
        throw new MutationError({
          kind: 'application',
          status: result.response?.status ?? null,
          detail: extractDetail(result.error),
          message:
            extractDetail(result.error) ??
            `Unarchive failed (${result.response?.status ?? 'no status'})`,
        });
      }
      return null;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [CONTACTS_KEY] });
    },
  });
}

// ── useOutreachLog ──────────────────────────────────────────────────────────

export type OutreachLogVars = { contactId: string; message: OutreachMessageCreate };

export function useOutreachLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: OutreachLogVars) => {
      const result = (await api.POST('/contacts/{contact_id}/outreach', {
        params: { path: { contact_id: vars.contactId } },
        body: toOutreachCreateBody(vars.message) as never,
      })) as OpenapiResult<OutreachMessage>;
      return throwIfError(result, 'Log failed');
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: [OUTREACH_KEY, vars.contactId] });
      qc.invalidateQueries({ queryKey: [OUTREACH_KEY, 'recent'] });
    },
  });
}
