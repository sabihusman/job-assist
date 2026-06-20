/**
 * Wire shapes for the Settings page (PR #32d).
 *
 * The backend `OperatorProfile` schema (apps/api/.../operator_profile.py)
 * is the source of truth. Mirror it here so the frontend doesn't reach
 * through `as unknown` on every read.
 *
 * Notably MISSING from the backend (per the PR #32c audit and confirmed
 * again here):
 *   - name (no column)
 *   - closed_channels list (separate table; no API)
 *
 * The Settings UI carries any such field as frontend-only state where the
 * spec asks for the control surface; on save they're filtered out of the
 * PUT body so we don't 422 on an `extra="forbid"` Pydantic config.
 * (The dead "Role family weights" sliders were removed — unwired control.)
 */

export type OperatorProfileRead = {
  id: number;
  looking_for_text: string;
  role_keywords: string[];
  geo_whitelist: string[];
  salary_floor_usd: number;
  // PR #43: nullable upper bound paired with the floor.
  salary_ceiling_usd: number | null;
  applicant_cap: number;
  // feat/tunable-per-company-cap: roles surfaced per company; 0 = disabled.
  per_company_cap: number;
  // Slice 2b: semantic blend weight for "Best fit (semantic)" sort; 0 = off.
  similarity_weight: number;
  // A3: applied-corpus RAG boost weight 0..1; 0 = off (no boost).
  applied_corpus_weight: number;
  staffing_firm_blocklist: string[];
  // PR #43: list of SeniorityLevel enum values to include. null or empty
  // means "include all levels".
  seniority_levels_included: string[] | null;
  created_at: string;
  updated_at: string;
};

export type OperatorProfileUpdate = Partial<{
  looking_for_text: string;
  role_keywords: string[];
  geo_whitelist: string[];
  salary_floor_usd: number;
  salary_ceiling_usd: number | null;
  applicant_cap: number;
  per_company_cap: number;
  similarity_weight: number;
  applied_corpus_weight: number;
  staffing_firm_blocklist: string[];
  seniority_levels_included: string[];
}>;

/**
 * SeniorityLevel enum values — must match apps/api/.../db/enums.py.
 * Labels shown in the UI are PM-specific because the underlying schema
 * is PM-specific (project is a PM job-search tool).
 */
export const SENIORITY_LEVELS: readonly { value: string; label: string }[] = [
  { value: 'intern', label: 'Intern' },
  { value: 'apm', label: 'APM' },
  { value: 'pm', label: 'PM' },
  { value: 'senior_pm', label: 'Senior PM' },
  { value: 'lead_pm', label: 'Lead PM' },
  { value: 'principal_pm', label: 'Principal PM' },
] as const;

/**
 * Read-only stub data for the Closed Channels section. Sourced from the
 * spec's example values until a real closed_channel endpoint exists.
 */
export type ClosedChannelStub = {
  company: string;
  reason: string;
  date: string; // already-formatted "MMM D"
};

export const CLOSED_CHANNELS_STUB: readonly ClosedChannelStub[] = [
  { company: 'MetaCorp', reason: 'Compensation cap below floor', date: 'Mar 12' },
  { company: 'BigBlueCo', reason: 'Onsite required, no remote option', date: 'Feb 28' },
] as const;
