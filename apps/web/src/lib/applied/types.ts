/**
 * Wire shape for `GET /outcomes` rows. The backend returns these as
 * `dict[str, Any]` so generated openapi types are wide — pinned here.
 *
 * `outcome_type` is the canonical stage string from the
 * Gmail-classifier vocabulary. Map → display label happens in
 * `lib/applied/stages.ts` so the UI can group similarly-shaped stages
 * (e.g. `recruiter_screen_invite` → "Recruiter screen").
 */
export type OutcomeEvent = {
  id: string;
  posting_id: string | null;
  received_at: string;
  stage: string;
  confidence: number | null;
  // feat/pipeline-outcome-cards: fields the Pipeline needs to label a card
  // without a per-posting link. `company_name` is the LEFT-JOINed
  // target_company name (usually null); `subject` carries the company for
  // unlinked rows; `from_domain` is the ATS vendor (last-resort label);
  // `email_thread_id` is the client-side group key. Present when the backend
  // is on the enriched /outcomes; optional so older payloads still typecheck.
  company_name?: string | null;
  subject?: string | null;
  from_domain?: string | null;
  email_thread_id?: string | null;
};

export type OutcomesListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: OutcomeEvent[];
};

export type AppliedSort = 'applied' | 'stage' | 'tier';
