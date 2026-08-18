import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { api } from '@/lib/api/client';
import {
  MAX_SWEEP_PASSES,
  PENDING_KEY,
  SWEEP_LIMIT,
  _resetCacheForTests,
  applyPendingChanges,
  clearPending,
  markPending,
} from '@/lib/settings/pendingRequeue';

/**
 * D-SETTINGS-REWIRE Part B tests: localStorage persistence (B1), and the
 * two-step apply sequence's loop semantics (B3-B5) — changed==0 is the stop
 * condition (legitimate on the first pass), `remaining` is never consulted,
 * the pass cap is enforced and surfaced, and the pending flag survives any
 * failure but clears on success.
 */

vi.mock('@/lib/api/client', () => ({
  api: { POST: vi.fn() },
}));

const mockPost = vi.mocked(api.POST);

const REEVAL_OK = {
  data: { evaluated: 2400, passed: 700, failed: 1700, by_rule: {} },
  error: undefined,
};

function sweepOk(changed: number) {
  return {
    data: { processed: 500, changed, skipped: 0, remaining: 1900, distribution: {} },
    error: undefined,
  };
}

function stored(): string[] {
  const raw = window.localStorage.getItem(PENDING_KEY);
  return raw ? (JSON.parse(raw) as string[]) : [];
}

beforeEach(() => {
  window.localStorage.clear();
  _resetCacheForTests();
  mockPost.mockReset();
});

afterEach(() => {
  clearPending();
});

describe('pending set persistence (B1)', () => {
  test('markPending writes localStorage, dedupes, and keeps canonical order', () => {
    markPending(['staffing_firm_blocklist']);
    markPending(['geo_whitelist', 'staffing_firm_blocklist']);
    // Canonical order, not insertion order.
    expect(stored()).toEqual(['geo_whitelist', 'staffing_firm_blocklist']);
  });

  test('pending set survives a simulated reload (cache dropped, re-read from storage)', () => {
    markPending(['salary_floor_usd']);
    _resetCacheForTests(); // simulate a fresh page load
    markPending(['role_keywords']);
    expect(stored()).toEqual(['role_keywords', 'salary_floor_usd']);
  });

  test('clearPending empties storage', () => {
    markPending(['geo_whitelist']);
    clearPending();
    expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
  });

  test('corrupt storage is treated as empty, not a crash', () => {
    window.localStorage.setItem(PENDING_KEY, '{not json');
    _resetCacheForTests();
    markPending(['geo_whitelist']);
    expect(stored()).toEqual(['geo_whitelist']);
  });
});

describe('applyPendingChanges (B3-B5)', () => {
  test('happy path: reeval, then sweeps until changed==0; flag cleared', async () => {
    markPending(['geo_whitelist', 'salary_floor_usd']);
    mockPost
      .mockResolvedValueOnce(REEVAL_OK as never)
      .mockResolvedValueOnce(sweepOk(120) as never)
      .mockResolvedValueOnce(sweepOk(3) as never)
      .mockResolvedValueOnce(sweepOk(0) as never);

    const progress: unknown[] = [];
    const result = await applyPendingChanges((p) => progress.push(p));

    expect(result).toEqual({
      ok: true,
      evaluated: 2400,
      passed: 700,
      passes: 3,
      totalChanged: 123,
      converged: true,
    });
    // B5: cleared only after both steps succeeded.
    expect(stored()).toEqual([]);
    // Step 1 called with no params; step 2 with the mandated body.
    expect(mockPost).toHaveBeenNthCalledWith(1, '/admin/postings/reeval-hard-rules', {});
    expect(mockPost).toHaveBeenNthCalledWith(2, '/admin/score/sweep', {
      body: { limit: SWEEP_LIMIT, only_unscored: false },
    });
    expect(mockPost).toHaveBeenCalledTimes(4);
  });

  test('changed==0 on the FIRST pass is success, not an error', async () => {
    markPending(['seniority_levels_included']);
    mockPost.mockResolvedValueOnce(REEVAL_OK as never).mockResolvedValueOnce(sweepOk(0) as never);

    const result = await applyPendingChanges(() => {});

    expect(result).toMatchObject({ ok: true, passes: 1, totalChanged: 0, converged: true });
    expect(stored()).toEqual([]);
  });

  test('reeval failure: stops before any sweep, pending flag kept (B4)', async () => {
    markPending(['geo_whitelist']);
    mockPost.mockResolvedValueOnce({ data: undefined, error: { detail: 'boom' } } as never);

    const result = await applyPendingChanges(() => {});

    expect(result).toMatchObject({ ok: false, failedStep: 'reeval' });
    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(stored()).toEqual(['geo_whitelist']);
  });

  test('sweep failure mid-loop: reports the failing pass, pending flag kept (B4)', async () => {
    markPending(['staffing_firm_blocklist']);
    mockPost
      .mockResolvedValueOnce(REEVAL_OK as never)
      .mockResolvedValueOnce(sweepOk(50) as never)
      .mockRejectedValueOnce(new Error('502 upstream'));

    const result = await applyPendingChanges(() => {});

    expect(result).toMatchObject({
      ok: false,
      failedStep: 'sweep',
      pass: 2,
      message: '502 upstream',
    });
    expect(stored()).toEqual(['staffing_firm_blocklist']);
  });

  test('never-converging sweep stops at the pass cap and surfaces it', async () => {
    markPending(['salary_ceiling_usd']);
    mockPost.mockResolvedValueOnce(REEVAL_OK as never);
    for (let i = 0; i < MAX_SWEEP_PASSES; i++) {
      mockPost.mockResolvedValueOnce(sweepOk(5) as never);
    }

    const result = await applyPendingChanges(() => {});

    // 1 reeval + exactly MAX_SWEEP_PASSES sweeps — never an 11th.
    expect(mockPost).toHaveBeenCalledTimes(1 + MAX_SWEEP_PASSES);
    expect(result).toMatchObject({
      ok: true,
      passes: MAX_SWEEP_PASSES,
      totalChanged: 5 * MAX_SWEEP_PASSES,
      converged: false,
    });
  });
});
