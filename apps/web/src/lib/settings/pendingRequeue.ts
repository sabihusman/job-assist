'use client';

import { useSyncExternalStore } from 'react';

import { api } from '@/lib/api/client';

/**
 * D-SETTINGS-REWIRE Part B: track which profile/hard-rule fields were SAVED
 * but not yet APPLIED to the existing corpus.
 *
 * Saving these six fields persists them to operator_profile, but nothing
 * recomputes existing rows: the hard-rule verdict (`hard_rule_failed`) is
 * stamped at ingest time only, and fit_score persists until a sweep. The
 * PendingChangesBanner + "Apply changes to queue." button close that gap by
 * running, in order:
 *   1. POST /admin/postings/reeval-hard-rules   (one pass over open rows)
 *   2. POST /admin/score/sweep {only_unscored: false, limit: 500}, looped
 *      until `changed == 0`, capped at MAX_SWEEP_PASSES.
 *
 * WHY localStorage (B1): the flag must survive page reloads, and there is no
 * backend column for it (backend changes are out of Part B's scope). This is
 * a single-operator app, so the same client-side persistence pattern as the
 * UI-scale control ('ui-scale-pct') and next-themes is appropriate. The
 * trade-off — a different browser would not see the pending flag — is
 * acceptable for a single-user tool.
 */

export const PENDING_KEY = 'ja-pending-requeue-v1';

/** Canonical order — keeps the banner's field list stable across save order. */
const FIELD_ORDER = [
  'role_keywords',
  'geo_whitelist',
  'salary_floor_usd',
  'salary_ceiling_usd',
  'seniority_levels_included',
  'staffing_firm_blocklist',
] as const;

export type PendingField = (typeof FIELD_ORDER)[number];

export const PENDING_FIELD_LABELS: Record<PendingField, string> = {
  role_keywords: 'Role keywords',
  geo_whitelist: 'Geography whitelist',
  salary_floor_usd: 'Salary floor',
  salary_ceiling_usd: 'Salary ceiling',
  seniority_levels_included: 'Seniority levels',
  staffing_firm_blocklist: 'Staffing firm blocklist',
};

const EMPTY: readonly PendingField[] = Object.freeze([]);

function isPendingField(value: unknown): value is PendingField {
  return typeof value === 'string' && (FIELD_ORDER as readonly string[]).includes(value);
}

// Snapshot cache — useSyncExternalStore needs a referentially-stable snapshot
// between notifications; re-parsing localStorage on every getSnapshot would
// return a fresh array each render and loop forever.
let cache: readonly PendingField[] | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

function read(): readonly PendingField[] {
  if (cache !== null) return cache;
  if (typeof window === 'undefined') return EMPTY;
  try {
    const raw = window.localStorage.getItem(PENDING_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    cache = Array.isArray(parsed) ? Object.freeze(parsed.filter(isPendingField)) : EMPTY;
  } catch {
    cache = EMPTY;
  }
  return cache;
}

/** Add saved-but-not-applied fields to the persistent pending set. */
export function markPending(fields: readonly PendingField[]): void {
  if (fields.length === 0) return;
  const merged = new Set([...read(), ...fields]);
  cache = Object.freeze(FIELD_ORDER.filter((f) => merged.has(f)));
  window.localStorage.setItem(PENDING_KEY, JSON.stringify(cache));
  notify();
}

/** B5: called ONLY by applyPendingChanges after both steps succeed. */
export function clearPending(): void {
  cache = EMPTY;
  if (typeof window !== 'undefined') window.localStorage.removeItem(PENDING_KEY);
  notify();
}

/** Test hook: drop the in-memory cache so the next read hits localStorage. */
export function _resetCacheForTests(): void {
  cache = null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Reactive view of the pending set (empty during SSR). */
export function usePendingFields(): readonly PendingField[] {
  return useSyncExternalStore(subscribe, read, () => EMPTY);
}

// ── Apply runner (B3/B4/B5) ─────────────────────────────────────────────

export const MAX_SWEEP_PASSES = 10;
export const SWEEP_LIMIT = 500;

export type ApplyProgress =
  | { step: 'reeval' }
  | { step: 'sweep'; pass: number; changed?: number; totalChanged: number };

export type ApplyResult =
  | {
      ok: true;
      evaluated: number;
      passed: number;
      passes: number;
      totalChanged: number;
      /** False when the MAX_SWEEP_PASSES cap was hit before changed==0. */
      converged: boolean;
    }
  | { ok: false; failedStep: 'reeval' | 'sweep'; pass?: number; message: string };

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  try {
    return JSON.stringify(error);
  } catch {
    return 'Unknown error';
  }
}

/**
 * Run the two-step apply sequence. On any failure: stop, report which step
 * failed, and LEAVE the pending flag set (B4). The flag clears only after
 * both steps complete (B5).
 *
 * Score-sweep loop semantics (read before touching):
 *   - `remaining` is CONSTANT when only_unscored=false — it is total_open
 *     minus this batch, NOT a progress counter. Never loop on it.
 *   - `changed` counts FINAL post-cap score movement; `changed == 0` is the
 *     stop condition, and 0 on the FIRST pass is a legitimate outcome
 *     (nothing needed to move), not an error.
 *   - Hard cap at MAX_SWEEP_PASSES regardless; the pass count is surfaced.
 *     Hitting the cap is reported as ok with converged=false (by then every
 *     open row has been re-scored several times over at limit=500) rather
 *     than kept-pending — the banner surfaces the cap so the operator can
 *     re-run if scores still look off.
 */
export async function applyPendingChanges(
  onProgress: (p: ApplyProgress) => void,
): Promise<ApplyResult> {
  onProgress({ step: 'reeval' });
  let evaluated = 0;
  let passed = 0;
  try {
    const { data, error } = await api.POST('/admin/postings/reeval-hard-rules', {});
    if (error) throw new Error(errorMessage(error));
    const body = data as { evaluated?: number; passed?: number } | undefined;
    evaluated = body?.evaluated ?? 0;
    passed = body?.passed ?? 0;
  } catch (e) {
    return { ok: false, failedStep: 'reeval', message: errorMessage(e) };
  }

  let totalChanged = 0;
  let converged = false;
  let passesRun = 0;
  for (let pass = 1; pass <= MAX_SWEEP_PASSES; pass++) {
    onProgress({ step: 'sweep', pass, totalChanged });
    try {
      const { data, error } = await api.POST('/admin/score/sweep', {
        body: { limit: SWEEP_LIMIT, only_unscored: false } as never,
      });
      if (error) throw new Error(errorMessage(error));
      const changed = (data as { changed?: number } | undefined)?.changed ?? 0;
      passesRun = pass;
      totalChanged += changed;
      onProgress({ step: 'sweep', pass, changed, totalChanged });
      if (changed === 0) {
        converged = true;
        break;
      }
    } catch (e) {
      return { ok: false, failedStep: 'sweep', pass, message: errorMessage(e) };
    }
  }

  clearPending(); // B5 — both steps done.
  return { ok: true, evaluated, passed, passes: passesRun, totalChanged, converged };
}
