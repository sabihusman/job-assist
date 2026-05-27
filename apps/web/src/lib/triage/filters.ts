import {
  type Ats,
  DEFAULT_SORT,
  type RemoteType,
  type RoleFamilyWire,
  type SortKey,
  type StateFilter,
  type TriageFilters,
} from '@/lib/triage/types';

/**
 * URL search params ↔ TriageFilters round-trip.
 *
 * The URL is the source of truth for filter state (so links are shareable
 * and back/forward navigation Just Works). This module is the only place
 * that knows the URL encoding; everything else reads `TriageFilters`.
 *
 * `state` defaults to `['triage']` when no `state` param is present —
 * the Triage page only ever wants triage-state postings, but the saved
 * filter rows override this to e.g. `['snoozed']`.
 */

export const DEFAULT_FILTERS: TriageFilters = {
  tier: [],
  ats: [],
  remote_type: [],
  role_family: [],
  state: ['triage'],
  include_snoozed_past_only: false,
  target_company_id: null,
  sort: DEFAULT_SORT,
  // PR #70 / Bestiary 5.13: bumped from 20 → 100 (API cap). With ~716
  // pending postings, the legacy 20-row default left the operator
  // unable to reach 96% of postings without explicit Load More clicks.
  // Page 1 at 100 covers the bulk; Load More fetches the next 100.
  limit: 100,
  offset: 0,
};

const VALID_ATS = new Set<Ats>(['greenhouse', 'lever', 'ashby', 'workday', 'icims']);
const VALID_REMOTE = new Set<RemoteType>(['remote', 'hybrid', 'onsite']);
const VALID_FAMILY = new Set<RoleFamilyWire>([
  'product_management',
  'product_owner',
  'product_marketing',
  'program_management',
  'other',
]);
const VALID_STATE = new Set<StateFilter>([
  'triage',
  'interested',
  'not_interested',
  'applied',
  'snoozed',
  // PR #50: cross-table state — backend maps to an EXISTS against
  // outcome_event, not posting_action.
  'rejected',
]);
const VALID_SORT = new Set<SortKey>([
  'newest',
  'oldest',
  'salary_high_to_low',
  'tier',
  'recently_posted',
  // PR #57: index-backed sort by fit_score DESC NULLS LAST.
  'best_fit',
]);

function intsFrom(values: string[]): number[] {
  const out: number[] = [];
  for (const v of values) {
    const n = Number.parseInt(v, 10);
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

function filterSet<T extends string>(values: string[], allowed: Set<T>): T[] {
  return values.filter((v): v is T => allowed.has(v as T));
}

/** Decode a URL `URLSearchParams` (or anything implementing `getAll`) into a TriageFilters. */
export function parseFilters(params: {
  getAll(name: string): string[];
  get(name: string): string | null;
}): TriageFilters {
  const stateRaw = filterSet(params.getAll('state'), VALID_STATE);
  // PR #49: parse ?sort=. Unknown / missing → DEFAULT_SORT. Same shape
  // as the other Set-membership validators above — keeps the parser
  // robust against URL tampering or stale links from before the enum
  // was extended.
  const sortRaw = params.get('sort');
  const sort: SortKey =
    sortRaw && VALID_SORT.has(sortRaw as SortKey) ? (sortRaw as SortKey) : DEFAULT_SORT;
  return {
    tier: intsFrom(params.getAll('tier')),
    ats: filterSet(params.getAll('ats'), VALID_ATS),
    remote_type: filterSet(params.getAll('remote_type'), VALID_REMOTE),
    role_family: filterSet(params.getAll('role_family'), VALID_FAMILY),
    // Default to triage if no state is set — the Triage page's whole reason
    // for existing. A non-empty user-supplied list wins.
    state: stateRaw.length > 0 ? stateRaw : ['triage'],
    include_snoozed_past_only: params.get('include_snoozed_past_only') === 'true',
    target_company_id: params.get('target_company_id'),
    sort,
    limit: Number.parseInt(params.get('limit') ?? '100', 10) || 100,
    offset: Number.parseInt(params.get('offset') ?? '0', 10) || 0,
  };
}

/** Encode a TriageFilters back into a URLSearchParams. */
export function encodeFilters(filters: Partial<TriageFilters>): URLSearchParams {
  const p = new URLSearchParams();
  for (const t of filters.tier ?? []) p.append('tier', String(t));
  for (const a of filters.ats ?? []) p.append('ats', a);
  for (const r of filters.remote_type ?? []) p.append('remote_type', r);
  for (const f of filters.role_family ?? []) p.append('role_family', f);
  for (const s of filters.state ?? []) p.append('state', s);
  if (filters.include_snoozed_past_only) p.set('include_snoozed_past_only', 'true');
  if (filters.target_company_id) p.set('target_company_id', filters.target_company_id);
  // PR #49: omit the default sort to keep clean URLs. ``?sort=newest``
  // and a missing param mean the same thing.
  if (filters.sort && filters.sort !== DEFAULT_SORT) p.set('sort', filters.sort);
  return p;
}

/**
 * Toggle one value in a string/number array filter. Used by FilterRow
 * when a chip is clicked — adds the value if absent, removes if present.
 */
export function toggleInArray<T>(arr: readonly T[], value: T): T[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
}
