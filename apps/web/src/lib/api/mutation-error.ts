/**
 * Typed mutation error for triage mutations (PR #58).
 *
 * Why this exists
 * ───────────────
 * The Vanta pass-action bug (PR #58 Part A) turned out to be a wire-body
 * field-name mismatch: the deployed frontend POSTed ``{kind, reason}``;
 * the FastAPI ``ActionCreate`` schema demands ``{action_type, reason}``.
 * The API returned 422 with a structured ``detail`` body, but the old
 * onError handler dropped the detail on the floor and showed a generic
 * "Action failed" toast — so the bug was invisible for weeks and every
 * pass / apply / reject action silently failed server-side.
 *
 * This module ships the post-mortem fix:
 *   1. A typed ``MutationError`` so the onError handler can rely on
 *      ``error.detail`` being present.
 *   2. ``extractDetail`` which parses FastAPI's two error shapes
 *      (``{detail: "..."}`` and ``{detail: [{msg, ...}]}``) into a
 *      single string the toast can render.
 *
 * No retry-on-5xx — the 503 hypothesis was a transient red herring;
 * the real error was always a deterministic 422.
 */

export class MutationError extends Error {
  readonly kind: 'application';
  readonly status: number | null;
  readonly detail: string | null;

  constructor(opts: {
    kind: 'application';
    status: number | null;
    detail: string | null;
    message: string;
  }) {
    super(opts.message);
    this.name = 'MutationError';
    this.kind = opts.kind;
    this.status = opts.status;
    this.detail = opts.detail;
  }
}

/**
 * Pull a ``detail`` string out of FastAPI's structured error body.
 *
 * Two shapes to handle:
 *   - ``{detail: "wrong_role"}``                 → "wrong_role"
 *   - ``{detail: [{loc, msg, type}, ...]}``      → first ``.msg``
 *   - anything else                              → ``null``
 */
export function extractDetail(error: unknown): string | null {
  if (error === null || error === undefined) return null;
  if (typeof error === 'string') return error;
  if (typeof error === 'object') {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === 'object' && 'msg' in first) {
        return String((first as { msg: unknown }).msg);
      }
    }
  }
  return null;
}
