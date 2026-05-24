'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import type { ContactsFilters, ContactsListResponse } from '@/lib/contacts/types';

/**
 * Hook for the outreach contacts list (PR #51).
 *
 * Lives in its own module — the contacts page doesn't ship with the
 * Triage / Applied bundles, and pulling the unrelated symbols would
 * inflate them.
 */

const CONTACTS_KEY = 'contacts' as const;

export const contactsKeys = {
  list: (params: Record<string, unknown> = {}) => [CONTACTS_KEY, params] as const,
};

function toQuery(filters: ContactsFilters): Record<string, unknown> {
  const q: Record<string, unknown> = {
    limit: filters.limit,
    offset: filters.offset,
  };
  if (filters.source_type.length) q.source_type = filters.source_type;
  if (filters.search.trim()) q.search = filters.search.trim();
  if (filters.include_archived) q.include_archived = true;
  return q;
}

export function useContacts(filters: ContactsFilters) {
  const query = toQuery(filters);
  return useQuery({
    queryKey: contactsKeys.list(query),
    queryFn: async () => {
      const { data, error } = await api.GET('/contacts', {
        params: { query: query as never },
      });
      if (error) throw error;
      return data as unknown as ContactsListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}
