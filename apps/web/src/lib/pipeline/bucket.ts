import { PIPELINE_STAGES, type PipelineStage, stageOf } from '@/lib/applied/stages';
import type { OutcomeEvent } from '@/lib/applied/types';
import { companyFromSubject } from '@/lib/pipeline/companyFromSubject';
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
  // Optional: applied-posting cards carry a company tier; outcome-derived
  // cards (feat/pipeline-outcome-cards) have no tier, so PipelineCard hides
  // the tier badge when it's absent.
  tier?: number;
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

/**
 * Pipeline bucketing driven by **outcome_events as first-class cards**
 * (feat/pipeline-outcome-cards). This is the path the Pipeline actually uses:
 * the operator's job-search history lives entirely in `outcome_event` (Gmail
 * crawl), and `job_posting_id` is uniformly NULL, so the old applied-posting
 * source rendered empty.
 *
 *   1. Drop noise — `stageOf` returns null for `unrelated` / `unclassified`.
 *   2. Group by `email_thread_id` (one Gmail thread = one application's
 *      lifecycle); rows without a thread stand alone. In prod ~186/191 threads
 *      are single-row, so grouping mostly collapses the handful of
 *      apply→reject chains and is otherwise per-event.
 *   3. The latest `received_at` row in a group sets the stage (latest-wins:
 *      a rejection after a confirmation moves the card to Rejected).
 *   4. Label: a linked `company_name` from any row in the group → company
 *      extracted from the latest subject → `from_domain` → the raw subject.
 *
 * Unlinked rows (target_company_id + posting_id both NULL) STILL render — the
 * fix for the drop bug where `bucketPostings` skipped every outcome on
 * `if (!o.posting_id) continue`.
 */
export function bucketOutcomes(outcomes: readonly OutcomeEvent[]): Buckets {
  const groups = new Map<string, OutcomeEvent[]>();
  for (const o of outcomes) {
    if (!stageOf(o.stage)) continue; // step 1: drop unrelated/unclassified
    const key = o.email_thread_id ? `t:${o.email_thread_id}` : `o:${o.id}`;
    const arr = groups.get(key);
    if (arr) arr.push(o);
    else groups.set(key, [o]);
  }

  const buckets = emptyBuckets();
  for (const [key, rows] of groups) {
    // step 3: latest received_at sets the stage.
    let latest = rows[0];
    for (const r of rows) {
      if (Date.parse(r.received_at) > Date.parse(latest.received_at)) latest = r;
    }
    const stage = stageOf(latest.stage);
    if (!stage) continue;

    // step 4: label fallback chain — company_name (any linked row) → a company
    // extracted from any row's subject (the apply email carries the clean
    // "applying to <X>"; a later rejection's subject is often vague) →
    // from_domain → raw subject.
    const linked = rows.find((r) => r.company_name)?.company_name ?? null;
    let extracted: string | null = null;
    for (const r of rows) {
      const e = companyFromSubject(r.subject);
      if (e) {
        extracted = e;
        break;
      }
    }
    const label = linked ?? extracted ?? latest.from_domain ?? latest.subject ?? 'Application';

    buckets[stage].push({
      id: key,
      companyName: label,
      roleTitle: latest.subject ?? '',
      roleFamily: null,
      appliedAt: latest.received_at,
    });
  }
  return buckets;
}
