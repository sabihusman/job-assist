import {
  type PipelineStage,
  STAGE_SORT_ORDER,
  stageBadgeTone,
  stageOf,
} from '@/lib/applied/stages';
import type { OutcomeEvent, ResolvedStatus } from '@/lib/applied/types';
import { companyFromSubject, roleFromSubject } from '@/lib/pipeline/companyFromSubject';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Unified Applied view (feat/applied-unified, builds on the #162 matcher).
 *
 * The manual Applied tab (resolved_status, ~4 rows) was near-useless next to
 * the real ~150-application history that lives in `outcome_event` (the Gmail
 * crawl). This module fuses BOTH into one deduplicated list where:
 *
 *   • The Pipeline (Gmail) is the AUTHORITATIVE membership source — every
 *     Gmail-detected application surfaces (the ~55 direct applications with no
 *     corpus posting included, since they're plain outcome rows).
 *   • A manual `application_state` is the AUTHORITATIVE STATUS OVERLAY wherever
 *     the operator set one — it wins over the latest Gmail stage.
 *
 * ── No-fanout guarantee (preserved exactly from #162 / #157) ────────────────
 * A Gmail row attaches to a corpus posting ONLY via `outcome_event.job_posting_id`,
 * which the #162 matcher sets on a SPECIFIC posting (specific role match) or
 * leaves NULL (the ~55 direct apps + the 3 ambiguous). We NEVER map a Gmail
 * signal to a posting by company. So the dedupe/overlay below can only ever
 * touch the one posting an email was specifically matched to — it is
 * structurally incapable of reintroducing the company-level fanout bug.
 *
 * ── Dedupe key ──────────────────────────────────────────────────────────────
 * `posting:<id>` when the application maps to a corpus posting (a manual
 * application_state row OR a Gmail thread linked via #162 — same posting ⇒ ONE
 * entry, source `both`); otherwise the Gmail group key `t:<thread>` / `o:<id>`.
 * Two Gmail threads linked to the same posting collapse into that one entry.
 *
 * ── Conflict resolution (manual vs Gmail) ───────────────────────────────────
 * `manualStatus` (the manual application_state — active funnel via the Applied
 * query, OR terminal rejected/accepted via the /outcomes `manual_status` join)
 * wins for the displayed status whenever present. Gmail's latest stage is the
 * fallback when no manual status exists. The Gmail timeline is always retained
 * as the per-application history regardless of which side owns the status.
 */

export type AppliedSource = 'manual' | 'gmail' | 'both';

export type UnifiedAppliedEntry = {
  /** Dedupe key (`posting:<id>` | `t:<thread>` | `o:<id>`). Stable React key. */
  key: string;
  company: string;
  role: string | null;
  /** Corpus posting this maps to (manual row or #162 link). null ⇒ Gmail-only,
   *  direct application — no Triage card to open, no manual controls. */
  postingId: string | null;
  source: AppliedSource;
  /** Authoritative manual overlay; null when the operator set no manual status. */
  manualStatus: ResolvedStatus | null;
  /** Latest Gmail lifecycle stage; null for a manual-only entry with no email. */
  gmailStage: PipelineStage | null;
  /** ms epoch used for sort=applied (manual applied_at, else latest received_at). */
  at: number;
  /** Gmail events in this group (chronological); empty for manual-only entries. */
  events: OutcomeEvent[];
  tier: number | null;
};

const MANUAL_STATUS_LABELS: Record<ResolvedStatus, string> = {
  applied: 'Applied',
  interview: 'Interview',
  offer: 'Offer',
  accepted: 'Accepted',
  rejected: 'Rejected',
};

/** Map a manual application_state status to a PipelineStage for badge tone +
 *  funnel sort ordering (manual has no Gmail stage of its own). */
function manualStatusToStage(status: ResolvedStatus): PipelineStage {
  switch (status) {
    case 'applied':
      return 'applied';
    case 'interview':
      return 'video'; // generic mid-funnel interview → pending tone
    case 'offer':
    case 'accepted':
      return 'offer';
    case 'rejected':
      return 'rejected';
  }
}

/** The PipelineStage that drives the entry's badge color + funnel sort:
 *  manual overlay when present, else the Gmail stage. */
export function entryStage(entry: UnifiedAppliedEntry): PipelineStage {
  if (entry.manualStatus) return manualStatusToStage(entry.manualStatus);
  return entry.gmailStage ?? 'applied';
}

/** Human label for the status badge — the manual status name wins; otherwise
 *  the Gmail stage's display label is resolved by the caller via STAGE_LABELS. */
export function entryStatusLabel(entry: UnifiedAppliedEntry): string | null {
  if (entry.manualStatus) return MANUAL_STATUS_LABELS[entry.manualStatus];
  return null; // caller falls back to STAGE_LABELS[entryStage(entry)]
}

export function entryTone(entry: UnifiedAppliedEntry) {
  return stageBadgeTone(entryStage(entry));
}

function latestOf(rows: readonly OutcomeEvent[]): OutcomeEvent {
  let latest = rows[0];
  for (const r of rows) {
    if (Date.parse(r.received_at) > Date.parse(latest.received_at)) latest = r;
  }
  return latest;
}

function deriveCompany(rows: readonly OutcomeEvent[], fallbackLatest: OutcomeEvent): string {
  const linked = rows.find((r) => r.company_name)?.company_name;
  if (linked) return linked;
  for (const r of rows) {
    const e = companyFromSubject(r.subject);
    if (e) return e;
  }
  return fallbackLatest.from_domain ?? fallbackLatest.subject ?? 'Application';
}

function deriveRole(rows: readonly OutcomeEvent[]): string | null {
  // The #162 link carries the REAL posting title — prefer it.
  const titled = rows.find((r) => r.posting_title)?.posting_title;
  if (titled) return titled;
  for (const r of rows) {
    const role = roleFromSubject(r.subject);
    if (role) return role;
  }
  return null;
}

/**
 * Fuse Gmail outcomes + manual applied-postings into one deduplicated list.
 *
 * @param outcomes        Job-related outcome_events (GET /outcomes?job_related=1).
 * @param manualPostings  Postings in the active Applied funnel (GET /postings?state=applied).
 */
export function unifyApplied(
  outcomes: readonly OutcomeEvent[],
  manualPostings: readonly PostingListItem[],
): UnifiedAppliedEntry[] {
  // 1. Group Gmail outcomes by thread (mirrors bucketOutcomes step 1-2). Drop
  //    the classifier's noise buckets (stageOf → null).
  const threadGroups = new Map<string, OutcomeEvent[]>();
  for (const o of outcomes) {
    if (!stageOf(o.stage)) continue;
    const key = o.email_thread_id ? `t:${o.email_thread_id}` : `o:${o.id}`;
    const arr = threadGroups.get(key);
    if (arr) arr.push(o);
    else threadGroups.set(key, [o]);
  }

  // 2. Re-key by linked posting so two threads matched to the SAME corpus
  //    posting collapse into one entry (dedupe key = posting:<id>).
  const byKey = new Map<string, { key: string; postingId: string | null; rows: OutcomeEvent[] }>();
  for (const [threadKey, rows] of threadGroups) {
    const postingId = rows.find((r) => r.posting_id)?.posting_id ?? null;
    const dedupeKey = postingId ? `posting:${postingId}` : threadKey;
    const existing = byKey.get(dedupeKey);
    if (existing) existing.rows.push(...rows);
    else byKey.set(dedupeKey, { key: dedupeKey, postingId, rows: [...rows] });
  }

  // Manual postings indexed by id (the active-funnel authoritative overlay).
  const manualMap = new Map<string, PostingListItem>();
  for (const p of manualPostings) manualMap.set(p.id, p);

  const consumed = new Set<string>(); // posting ids represented by a Gmail group
  const entries: UnifiedAppliedEntry[] = [];

  for (const { key, postingId, rows } of byKey.values()) {
    const latest = latestOf(rows);
    const gmailStage = stageOf(latest.stage);
    if (!gmailStage) continue;

    const manualPosting = postingId ? (manualMap.get(postingId) ?? null) : null;
    // Manual overlay: prefer the active-funnel resolved_status; fall back to the
    // /outcomes manual_status join (covers terminal rejected/accepted excluded
    // from the active Applied query). Both are posting-specific by construction.
    const manualStatus =
      manualPosting?.state.resolved_status ??
      rows.find((r) => r.manual_status)?.manual_status ??
      null;

    if (postingId) consumed.add(postingId);

    const events = [...rows].sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at));

    entries.push({
      key,
      company: manualPosting?.company.name ?? deriveCompany(rows, latest),
      role: manualPosting?.role.title ?? deriveRole(rows),
      postingId,
      source: manualStatus ? 'both' : 'gmail',
      manualStatus,
      gmailStage,
      at: Date.parse(latest.received_at),
      events,
      tier: manualPosting?.company.tier ?? null,
    });
  }

  // 3. Manual-only: manual applications never matched to any Gmail thread.
  for (const p of manualPostings) {
    if (consumed.has(p.id)) continue;
    const appliedAtIso = p.state.current_at ?? p.first_seen_at;
    entries.push({
      key: `posting:${p.id}`,
      company: p.company.name,
      role: p.role.title,
      postingId: p.id,
      source: 'manual',
      manualStatus: p.state.resolved_status ?? 'applied',
      gmailStage: null,
      at: Date.parse(appliedAtIso),
      events: [],
      tier: p.company.tier ?? null,
    });
  }

  return entries;
}

export type UnifiedSort = 'applied' | 'stage' | 'tier';

/** Stable, in-place-safe sort of unified entries by the chosen key. */
export function sortUnified(
  entries: readonly UnifiedAppliedEntry[],
  sort: UnifiedSort,
): UnifiedAppliedEntry[] {
  const copy = [...entries];
  if (sort === 'applied') {
    copy.sort((a, b) => b.at - a.at);
  } else if (sort === 'stage') {
    copy.sort((a, b) => STAGE_SORT_ORDER[entryStage(a)] - STAGE_SORT_ORDER[entryStage(b)]);
  } else if (sort === 'tier') {
    copy.sort((a, b) => (a.tier ?? 99) - (b.tier ?? 99));
  }
  return copy;
}
