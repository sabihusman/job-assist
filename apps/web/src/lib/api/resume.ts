'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api/client';

/**
 * Hooks for resume-version tracking (feat/resume-version-tracking):
 *   * useResumeVersions   — GET /resume-versions (picker + manager list)
 *   * useCreateResumeVersion — POST /admin/resume-versions
 *   * useResumeAnalytics  — GET /admin/resume-analytics
 *
 * Kept in their own module so Triage (which only needs the picker list)
 * doesn't pull the analytics symbols into its bundle.
 */

const RESUME_VERSIONS_KEY = 'resume-versions' as const;
const RESUME_ANALYTICS_KEY = 'resume-analytics' as const;

export type ResumeVersion = {
  id: string;
  label: string;
  angle: string | null;
  snapshot_text: string | null;
  notes: string | null;
  created_at: string;
};

export type ResumeVersionCreate = {
  label: string;
  angle?: string | null;
  snapshot_text?: string | null;
  notes?: string | null;
};

export type ResumeAnalytics = {
  by_version: Array<{
    resume_version_id: string;
    label: string;
    angle: string | null;
    applications: number;
    companies: number;
    companies_rejected: number;
    companies_confirmed: number;
  }>;
  funnel: Array<{ label: string; outcome_type: string; companies: number }>;
  ambiguous_companies: Array<{ company_id: string; distinct_resume_versions: number }>;
  attribution_note: string;
};

/** List all resume versions (newest first). Powers the picker + manager. */
export function useResumeVersions(enabled = true) {
  return useQuery({
    queryKey: [RESUME_VERSIONS_KEY],
    queryFn: async () => {
      const { data, error } = await api.GET('/resume-versions');
      if (error) throw error;
      return data as unknown as { total: number; items: ResumeVersion[] };
    },
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}

/** Create a resume version. Invalidates the list on success. */
export function useCreateResumeVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ResumeVersionCreate) => {
      const { data, error } = await api.POST('/admin/resume-versions', {
        body: body as never,
      });
      if (error) throw error;
      return data as unknown as ResumeVersion;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [RESUME_VERSIONS_KEY] });
    },
  });
}

/** Resume → outcome analytics (company-level; see attribution_note). */
export function useResumeAnalytics() {
  return useQuery({
    queryKey: [RESUME_ANALYTICS_KEY],
    queryFn: async () => {
      const { data, error } = await api.GET('/admin/resume-analytics');
      if (error) throw error;
      return data as unknown as ResumeAnalytics;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
}
