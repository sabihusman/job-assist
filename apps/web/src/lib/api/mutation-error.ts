/**
 * Typed mutation error + one-shot retry helper for triage mutations (PR #58).
 *
 * Why this exists
 * ───────────────
 * The Vanta pass-action bug (PR #58 Part A) was a Railway cold-start 503,
 * not an application defect. The keepalive cron is throttled by GitHub
 * Actions (~2-hour gaps despite a 5-minute cron — see PR description),
 * leaving windows where the Railway service has scaled to zero and the
 * edge returns 503 before reaching FastAPI. A single retry after a short
 * delay almost always lands on a warm worker.
 *
 * Behavior contract
 * ─────────────────
 * - 5xx response → retry once after a 2-second delay. If retry succeeds,
 *   complete the optimistic UI flow as if the first call had worked.
 *   If retry also fails, throw ``MutationError`` with
 *   ``kind="transient"``.
 * - 4xx response → no retry. Throw ``MutationError`` with
 *   ``kind="application"`` and the structured ``detail`` from the body
 *   when present.
 * - Network error (fetch threw) → treated as transient. Retry once,
 *   then throw with ``kind="transient"``.
 *
 * The consumer (useRecordAction + its page-level onError) inspects
 * ``error.kind`` to pick the right toast. No app code below this layer
 * has to know about the retry — it's invisible on success.
 */

export type MutationErrorKind = 'transient' | 'application';

export class MutationError extends Error {
  readonly kind: MutationErrorKind;
  readonly status: number | null;
  readonly detail: string | null;

  constructor(opts: {
    kind: MutationErrorKind;
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

// Exported for unit-test override. Tests pass a 0-delay sleep so they
// don't actually wait 2 seconds between attempts.
export const DEFAULT_RETRY_DELAY_MS = 2000;

export type RetryableResponse<T> = {
  data?: T | undefined;
  error?: unknown;
  response?: Response | undefined;
};

/**
 * Run an openapi-fetch-style operation with one transient-failure retry.
 *
 * The callable is expected to return ``{ data, error, response }`` —
 * openapi-fetch's shape. ``error`` is the parsed JSON body of a non-2xx
 * response; ``response`` carries the status code. We classify by status:
 *
 *   5xx (or network) → retry once after ``delayMs`` → if still 5xx,
 *                       throw MutationError(kind=transient)
 *   4xx              → throw MutationError(kind=application) immediately
 *   2xx              → return ``data``
 *
 * ``delayMs`` is injectable so unit tests can run instantly.
 */
export async function runWithTransientRetry<T>(
  fn: () => Promise<RetryableResponse<T>>,
  opts: { delayMs?: number; sleep?: (ms: number) => Promise<void> } = {},
): Promise<T> {
  const delayMs = opts.delayMs ?? DEFAULT_RETRY_DELAY_MS;
  const sleep = opts.sleep ?? defaultSleep;

  let lastStatus: number | null = null;
  let lastDetail: string | null = null;

  for (let attempt = 0; attempt < 2; attempt++) {
    let result: RetryableResponse<T>;
    try {
      result = await fn();
    } catch (caught) {
      // Network failure (fetch threw). Treat as transient.
      lastStatus = null;
      lastDetail = caught instanceof Error ? caught.message : String(caught);
      if (attempt === 0) {
        await sleep(delayMs);
        continue;
      }
      throw new MutationError({
        kind: 'transient',
        status: null,
        detail: lastDetail,
        message: lastDetail || 'Network error',
      });
    }

    const status = result.response?.status ?? null;
    if (!result.error && result.data !== undefined) {
      return result.data;
    }

    lastStatus = status;
    lastDetail = extractDetail(result.error);

    if (status !== null && status >= 400 && status < 500) {
      // 4xx — application error, no retry.
      throw new MutationError({
        kind: 'application',
        status,
        detail: lastDetail,
        message: lastDetail ?? `Application error (${status})`,
      });
    }

    // 5xx or unknown (no response object) — retry once, then give up.
    if (attempt === 0) {
      await sleep(delayMs);
    }
  }

  // Retry exhausted on transient failure.
  throw new MutationError({
    kind: 'transient',
    status: lastStatus,
    detail: lastDetail,
    message: lastDetail ?? `Server error${lastStatus ? ` (${lastStatus})` : ''}`,
  });
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Pull a ``detail`` string out of FastAPI's structured error body, if any. */
function extractDetail(error: unknown): string | null {
  if (error === null || error === undefined) return null;
  if (typeof error === 'string') return error;
  if (typeof error === 'object') {
    // FastAPI HTTPException: { detail: "..." }. Validation errors:
    // { detail: [{loc, msg, type}, ...] } — surface the first msg.
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
