/**
 * Wire shape returned by `GET /companies` (PR #30a).
 *
 * The Python endpoint returns `dict[str, Any]` so generated openapi
 * types widen to `Record<string, never>`; pin the actual fields here.
 *
 * Notes for #32c:
 *   - `ats_set` lists the distinct ATSes seen on this company's
 *     postings — the company itself doesn't have an authoritative ATS
 *     field. In practice 1 entry per row.
 *   - PR #71 added `ats` / `ats_handle` / `notes` so the Companies
 *     page can surface paused-state. `ats_handle === null` while
 *     `ats !== 'unknown'` is the canonical "soft-paused" signal
 *     (PR #65 Atlassian case).
 */
export type CompanyListItem = {
  id: string;
  name: string;
  domain: string | null;
  description: string | null;
  tier: number | null;
  ats_set: string[];
  active_postings: number;
  total_postings: number;
  ats: string | null;
  ats_handle: string | null;
  notes: string | null;
};

export type CompaniesListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: CompanyListItem[];
};
