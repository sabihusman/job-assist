import { describe, expect, test, vi } from 'vitest';

import {
  MutationError,
  type RetryableResponse,
  runWithTransientRetry,
} from '@/lib/api/mutation-error';

/**
 * Unit tests for the PR #58 retry/error helper.
 *
 * All tests pass a zero-delay ``sleep`` so the 2-second retry pause
 * doesn't actually fire — we still want to assert that ``sleep`` was
 * called between attempts, which proves the retry path executed.
 */
describe('runWithTransientRetry', () => {
  const noSleep = vi.fn(async (_ms: number) => {
    /* instant */
  });

  test('5xx then 200 → returns data, no error surfaced', async () => {
    const sleep = vi.fn(async (_ms: number) => {});
    const fn = vi
      .fn<() => Promise<RetryableResponse<{ ok: true }>>>()
      .mockResolvedValueOnce({
        error: { detail: 'cold start' },
        response: new Response(null, { status: 503 }),
      })
      .mockResolvedValueOnce({
        data: { ok: true },
        response: new Response(null, { status: 200 }),
      });

    const result = await runWithTransientRetry(fn, { delayMs: 0, sleep });

    expect(result).toEqual({ ok: true });
    expect(fn).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(0);
  });

  test('both calls 5xx → throws transient MutationError', async () => {
    const fn = vi.fn<() => Promise<RetryableResponse<unknown>>>().mockResolvedValue({
      error: { detail: 'still down' },
      response: new Response(null, { status: 503 }),
    });

    await expect(runWithTransientRetry(fn, { delayMs: 0, sleep: noSleep })).rejects.toMatchObject({
      name: 'MutationError',
      kind: 'transient',
      status: 503,
      detail: 'still down',
    });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  test('network throw then 200 → returns data', async () => {
    const sleep = vi.fn(async (_ms: number) => {});
    const fn = vi
      .fn<() => Promise<RetryableResponse<{ ok: true }>>>()
      .mockRejectedValueOnce(new Error('fetch failed'))
      .mockResolvedValueOnce({
        data: { ok: true },
        response: new Response(null, { status: 200 }),
      });

    const result = await runWithTransientRetry(fn, { delayMs: 0, sleep });

    expect(result).toEqual({ ok: true });
    expect(sleep).toHaveBeenCalledTimes(1);
  });

  test('two network throws → throws transient MutationError with last message', async () => {
    const fn = vi
      .fn<() => Promise<RetryableResponse<unknown>>>()
      .mockRejectedValue(new Error('network down'));

    const err = (await runWithTransientRetry(fn, {
      delayMs: 0,
      sleep: noSleep,
    }).catch((e: unknown) => e)) as MutationError;

    expect(err).toBeInstanceOf(MutationError);
    expect(err.kind).toBe('transient');
    expect(err.status).toBeNull();
    expect(err.detail).toBe('network down');
    expect(fn).toHaveBeenCalledTimes(2);
  });

  test('4xx with structured detail → throws application MutationError, NO retry', async () => {
    const sleep = vi.fn(async (_ms: number) => {});
    const fn = vi.fn<() => Promise<RetryableResponse<unknown>>>().mockResolvedValue({
      error: { detail: 'reason_required' },
      response: new Response(null, { status: 422 }),
    });

    const err = (await runWithTransientRetry(fn, { delayMs: 0, sleep }).catch(
      (e: unknown) => e,
    )) as MutationError;

    expect(err).toBeInstanceOf(MutationError);
    expect(err.kind).toBe('application');
    expect(err.status).toBe(422);
    expect(err.detail).toBe('reason_required');
    // Critical: no retry on 4xx.
    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  test('4xx with detail array → extracts first .msg', async () => {
    const fn = vi.fn<() => Promise<RetryableResponse<unknown>>>().mockResolvedValue({
      error: {
        detail: [{ loc: ['body', 'reason'], msg: 'field required', type: 'value_error.missing' }],
      },
      response: new Response(null, { status: 422 }),
    });

    const err = (await runWithTransientRetry(fn, {
      delayMs: 0,
      sleep: noSleep,
    }).catch((e: unknown) => e)) as MutationError;

    expect(err.kind).toBe('application');
    expect(err.detail).toBe('field required');
  });

  test('first call succeeds → no retry, no sleep', async () => {
    const sleep = vi.fn(async (_ms: number) => {});
    const fn = vi.fn<() => Promise<RetryableResponse<{ ok: true }>>>().mockResolvedValue({
      data: { ok: true },
      response: new Response(null, { status: 200 }),
    });

    const result = await runWithTransientRetry(fn, { delayMs: 0, sleep });

    expect(result).toEqual({ ok: true });
    expect(fn).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  test('MutationError carries kind/status/detail on the instance', () => {
    const err = new MutationError({
      kind: 'application',
      status: 400,
      detail: 'bad',
      message: 'bad',
    });
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe('MutationError');
    expect(err.kind).toBe('application');
    expect(err.status).toBe(400);
    expect(err.detail).toBe('bad');
    expect(err.message).toBe('bad');
  });
});
