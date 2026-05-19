import { PIPELINE_STAGES, type PipelineStage, stageOf } from '@/lib/applied/stages';
import type { OutcomeEvent } from '@/lib/applied/types';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Client-side bucketing for the Pipeline kanban.
 *
 * Builds a `Record<PipelineStage, ApplicationCard[]>` from the cross-
 * product of applied postings and outcome events:
 *
 *   1. For each posting, find the most-recent outcome event whose
 *      classified `outcome_type` maps to a known stage.
 *   2. If no outcome event maps to a stage → posting is in APPLIED.
 *   3. Ghosted heuristic: applied >30d ago AND no outcomes → GHOSTED.
 *
 * The applied/ghosted overlap is resolved in step 3 — postings without
 * outcomes that have been sitting >30d move from APPLIED → GHOSTED.
 *
 * The empty bucket structure is always returned in PIPELINE_STAGES
 * order so consumers don't have to handle missing keys.
 */

export type ApplicationCard = {
  id: string;
  tier: number;
  companyName: string;
  roleTitle: string;
  roleFamily: string | null;
  appliedAt: string;
};

const GHOSTED_AFTER_DAYS = 30;
const GHOSTED_AFTER_MS = GHOSTED_AFTER_DAYS * 24 * 60 * 60 * 1000;

export type Buckets = Record<PipelineStage, ApplicationCard[]>;

export function emptyBuckets(): Buckets {
  // Construct explicitly so TS sees every key is populated — Object.fromEntries
  // widens to `Record<string, never[]>` which doesn't match `Buckets`.
  const out = {} as Buckets;
  for (const s of PIPELINE_STAGES) out[s] = [];
  return out;
}

export function bucketPostings(
  postings: readonly PostingListItem[],
  outcomes: readonly OutcomeEvent[],
  now: number = Date.now(),
): Buckets {
  // Index outcomes → latest stage per posting.
  const latestByPosting = new Map<string, { stage: PipelineStage; ts: number }>();
  for (const o of outcomes) {
    if (!o.posting_id) continue;
    const stage = stageOf(o.stage);
    if (!stage) continue;
    const ts = Date.parse(o.received_at);
    const prev = latestByPosting.get(o.posting_id);
    if (!prev || ts > prev.ts) {
      latestByPosting.set(o.posting_id, { stage, ts });
    }
  }

  const buckets = emptyBuckets();
  for (const p of postings) {
    const appliedIso = p.state.current_at ?? p.first_seen_at;
    const card: ApplicationCard = {
      id: p.id,
      tier: p.company.tier ?? 4,
      companyName: p.company.name,
      roleTitle: p.role.title,
      roleFamily: p.role.family,
      appliedAt: appliedIso,
    };
    const latest = latestByPosting.get(p.id);
    if (latest) {
      buckets[latest.stage].push(card);
      continue;
    }
    // No mapped outcome — bucket as APPLIED or GHOSTED based on age.
    const ageMs = now - Date.parse(appliedIso);
    if (ageMs > GHOSTED_AFTER_MS) {
      buckets.ghosted.push(card);
    } else {
      buckets.applied.push(card);
    }
  }
  return buckets;
}
