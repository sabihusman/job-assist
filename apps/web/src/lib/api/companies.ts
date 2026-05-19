'use client';

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api/client';
import type { CompaniesListResponse } from '@/lib/companies/types';

export function useCompanies() {
  return useQuery({
    queryKey: ['companies', { limit: 100, offset: 0 }] as const,
    queryFn: async () => {
      const { data, error } = await api.GET('/companies', {
        params: { query: { limit: 100, offset: 0 } as never },
      });
      if (error) throw error;
      return data as unknown as CompaniesListResponse;
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}
