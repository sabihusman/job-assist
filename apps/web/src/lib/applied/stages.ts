/**
 * Outcome-stage taxonomy used by Applied / Pipeline / Stats.
 *
 * The backend's `outcome_event.outcome_type` is a fine-grained PG enum
 * (PR #25/#31): `application_confirmation`, `recruiter_screen_invite`,
 * `phone_interview_invite`, `video_interview_invite`,
 * `onsite_interview_invite`, `panel_interview_invite`, `offer`,
 * `rejection_pre_screen`, `rejection_post_screen`,
 * `rejection_post_interview`, `withdrawn`, `unrelated`, `unclassified`.
 *
 * The UI buckets these into a coarser pipeline. This module is the
 * single source of truth for both the bucket mapping and the display
 * label / badge color.
 */

export type PipelineStage =
  | 'applied'
  | 'recruiter'
  | 'phone'
  | 'video'
  | 'onsite'
  | 'offer'
  | 'rejected'
  | 'ghosted';

export const PIPELINE_STAGES: readonly PipelineStage[] = [
  'applied',
  'recruiter',
  'phone',
  'video',
  'onsite',
  'offer',
  'rejected',
  'ghosted',
] as const;

export const STAGE_LABELS: Record<PipelineStage, string> = {
  applied: 'Applied',
  recruiter: 'Recruiter screen',
  phone: 'Phone interview',
  video: 'Video interview',
  onsite: 'Onsite',
  offer: 'Offer',
  rejected: 'Rejected',
  ghosted: 'Ghosted',
};

/** Bucket the outcome_event.outcome_type string into a PipelineStage. */
export function stageOf(outcomeType: string | null | undefined): PipelineStage | null {
  if (!outcomeType) return null;
  const v = outcomeType.toLowerCase();
  if (v === 'application_confirmation' || v === 'applied') return 'applied';
  if (v.startsWith('recruiter')) return 'recruiter';
  if (v.startsWith('phone')) return 'phone';
  if (v.startsWith('video')) return 'video';
  if (v.startsWith('onsite') || v.startsWith('panel')) return 'onsite';
  if (v === 'offer') return 'offer';
  if (v.startsWith('rejection') || v === 'withdrawn') return 'rejected';
  return null;
}

/**
 * Token name to use for the stage's badge color. The UI's CSS variables
 * cover `positive` / `negative` / `pending` / `muted` and these stages
 * map cleanly:
 *
 *  applied   → positive (initial green dot)
 *  recruiter → pending (amber, interview pipeline)
 *  phone     → pending
 *  video     → pending
 *  onsite    → pending
 *  offer     → positive (stronger; same hue as applied)
 *  rejected  → negative
 *  ghosted   → muted
 */
export function stageBadgeTone(
  stage: PipelineStage,
): 'positive' | 'negative' | 'pending' | 'muted' {
  if (stage === 'applied' || stage === 'offer') return 'positive';
  if (stage === 'rejected') return 'negative';
  if (stage === 'ghosted') return 'muted';
  return 'pending';
}

/**
 * For sort=stage on the Applied page: stages descending in funnel
 * importance — offer first, ghosted last.
 */
export const STAGE_SORT_ORDER: Record<PipelineStage, number> = {
  offer: 0,
  onsite: 1,
  video: 2,
  phone: 3,
  recruiter: 4,
  applied: 5,
  rejected: 6,
  ghosted: 7,
};
