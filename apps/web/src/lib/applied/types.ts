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
};

export type OutcomesListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: OutcomeEvent[];
};

export type AppliedSort = 'applied' | 'stage' | 'tier';
