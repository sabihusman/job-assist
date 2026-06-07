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
  // feat/applied-company-tracking: company linkage (posting_id is uniformly
  // NULL). Drives the Companies OUTCOMES column by company.
  target_company_id?: string | null;
  // feat/pipeline-detail: ~200-char Gmail preview (no email body is stored).
  raw_snippet?: string | null;
  // feat/applied-unified: posting-specific overlay, set ONLY when this email
  // was matched to ONE corpus posting via the #162 no-fanout matcher (NULL for
  // the unlinked majority). `posting_title` is the real role; `manual_status`
  // is the AUTHORITATIVE manual application_state on that posting — it wins
  // over the Gmail stage in the unified Applied view. Posting-specific by
  // construction, so it can never reintroduce the company-level fanout (#157).
  posting_title?: string | null;
  manual_status?: ResolvedStatus | null;
};

// feat/manual-application-status: the manual lifecycle stage (mirrors the
// Python APPLICATION_STATUS_VALUES). Canonical source is the ORM model.
export type ResolvedStatus = 'applied' | 'interview' | 'offer' | 'accepted' | 'rejected';

export type OutcomesListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: OutcomeEvent[];
};

export type AppliedSort = 'applied' | 'stage' | 'tier';
