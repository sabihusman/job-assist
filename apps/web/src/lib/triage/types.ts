/**
 * Triage-specific wire types.
 *
 * The FastAPI endpoints (`/postings`, `/postings/{id}`, `/stats/calibration`)
 * return `dict[str, Any]` on the server side so `openapi-typescript`
 * generates loose `Record<string, never>` shapes. We keep the wire format
 * stable in code by defining the contract here; #32c can extract these
 * to a shared/ folder when the other pages start consuming them.
 *
 * Single source of truth for the enum values is `apps/api/db/enums.py`.
 * The Python side is canonical — these unions must mirror it.
 */

export type ActionType = 'interested' | 'not_interested' | 'applied' | 'snoozed' | 'reset';

export type ActionReason =
  | 'wrong_role'
  | 'wrong_location'
  | 'comp_too_low'
  | 'wrong_industry'
  | 'wrong_stage'
  | 'already_rejected_here'
  | 'just_not_feeling_it'
  // PR #43: seniority-band reasons.
  | 'too_senior'
  | 'too_junior';

export type RemoteType = 'remote' | 'hybrid' | 'onsite';

export type Ats = 'greenhouse' | 'lever' | 'ashby';

export type RoleFamilyWire =
  | 'product_management'
  | 'product_owner'
  | 'product_marketing'
  | 'program_management'
  | 'other';

export type StateFilter = 'triage' | 'interested' | 'not_interested' | 'applied' | 'snoozed';

// ── Embedded sub-shapes ──────────────────────────────────────────────────

export type CompanyEmbedded = {
  id: string | null;
  name: string;
  domain: string | null;
  description: string | null;
  tier: number | null;
};

export type RoleEmbedded = {
  title: string;
  family: string | null;
  department: string | null;
  team: string | null;
  seniority: string | null;
};

export type SalaryEmbedded = {
  min: number | null;
  max: number | null;
  currency: string | null;
  period: string | null;
};

export type SourceEmbedded = {
  ats: string;
  url: string | null;
};

export type StateEmbedded = {
  current: ActionType | null;
  reason: ActionReason | null;
  snooze_until: string | null;
  current_at: string | null;
};

// ── List + detail items ──────────────────────────────────────────────────

export type PostingListItem = {
  id: string;
  company: CompanyEmbedded;
  role: RoleEmbedded;
  location_raw: string | null;
  locations_normalized: string[];
  remote_type: RemoteType | string | null;
  salary: SalaryEmbedded | null;
  source: SourceEmbedded;
  first_seen_at: string;
  score: number | null;
  state: StateEmbedded;
};

export type DivisionEmbedded = {
  id: string;
  department: string | null;
  team: string | null;
  description: string | null;
};

export type PostingActionRow = {
  id: string;
  action_type: ActionType;
  reason: ActionReason | null;
  snooze_until: string | null;
  notes: string | null;
  created_at: string;
};

export type PostingDetail = PostingListItem & {
  description_markdown: string | null;
  // Gemini-generated operator-focused summary (PR #41/#42). NULL until
  // the enrichment sweep has visited the row.
  jd_summary_markdown: string | null;
  division: DivisionEmbedded | null;
  posted_at: string | null;
  last_seen_at: string | null;
  closed_at: string | null;
  state_history: PostingActionRow[];
};

export type PostingsListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: PostingListItem[];
};

// ── Calibration ──────────────────────────────────────────────────────────

export type TopRejectedRoleFamily = {
  role_family: string;
  count: number;
};

export type CalibrationResponse = {
  window: { since: string; until: string };
  surfaced: number;
  interested: number;
  interested_rate: number | null;
  applied: number;
  rejected_by_you: number;
  top_rejected_role_families: TopRejectedRoleFamily[];
};

// ── Filter envelope used by useTriagePostings + URL <-> state ─────────────

export type TriageFilters = {
  tier: number[];
  ats: Ats[];
  remote_type: RemoteType[];
  role_family: RoleFamilyWire[];
  state: StateFilter[];
  include_snoozed_past_only: boolean;
  target_company_id: string | null;
  limit: number;
  offset: number;
};
