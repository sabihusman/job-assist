'use client';

import { toast } from 'sonner';

import { MutationError } from '@/lib/api/mutation-error';

/**
 * Centralized error-toast surface for mutation failures (PR #73 /
 * Bestiary 5.14).
 *
 * Why this exists
 * ───────────────
 * Before PR #73, every mutation hook's onError did the same
 * "MutationError ? detail : generic fallback" dance inline, and
 * non-HTTP exceptions (the cache-collision TypeError from PR #69,
 * for example) only surfaced their message in dev builds — production
 * showed an opaque "Action couldn't be completed." with no actionable
 * info. Plus every toast persisted forever because sonner's default
 * ``duration`` for ``toast.error`` is ``Infinity``.
 *
 * Behavior contract
 * ─────────────────
 * 1. ``MutationError`` with a ``detail`` string → that string verbatim.
 *    These are FastAPI-shaped 4xx bodies; the operator sees the
 *    actual server reason ("reason_required_for_not_interested",
 *    "limit must be 1..100", etc).
 * 2. Native ``Error`` (or anything with a ``message`` string) →
 *    "{fallback} — {err.message}". The fallback gives context for
 *    WHAT failed; the message gives context for WHY. Both layers are
 *    useful: a TypeError from a future cache-shape regression would
 *    surface in production verbatim instead of hiding.
 * 3. Anything else (null, undefined, plain objects without ``message``)
 *    → just the fallback. Operator at least knows what tried to
 *    happen.
 *
 * Every path also ``console.error``s the raw error so the operator
 * can paste it into a bug report. The toast carries an explicit 4500ms
 * duration (longer than success toasts to give reading time) and a
 * close button (sonner ``closeButton`` on the Toaster).
 */
export function showErrorToast(err: unknown, fallback: string): void {
  // Structured FastAPI error — preserved from PR #58.
  if (err instanceof MutationError && err.detail) {
    toast.error(err.detail, { duration: 4500 });
    console.error('[mutation]', fallback, err);
    return;
  }
  // Native Error with a message — surface in BOTH dev and prod.
  // PR #68 / Bestiary 5.12 was invisible for weeks precisely because
  // this branch only logged in dev. Now production sees it too.
  if (err instanceof Error && err.message) {
    toast.error(`${fallback} — ${err.message}`, { duration: 4500 });
    console.error('[mutation]', fallback, err);
    return;
  }
  // True fallback. err is null/undefined/plain object/etc.
  toast.error(fallback, { duration: 4500 });
  console.error('[mutation]', fallback, err);
}
