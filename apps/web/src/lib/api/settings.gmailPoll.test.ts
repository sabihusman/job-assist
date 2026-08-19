import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { api } from '@/lib/api/client';
import { pollGmailJob } from '@/lib/api/settings';

/**
 * D-SETTINGS-REWIRE C3: the Gmail backfill row fires a 202 then polls
 * GET /admin/gmail/jobs/{job_id} until the row leaves 'running'. These tests
 * pin the poll loop: terminal-success returns the row, terminal-failure
 * throws with the row's error_message, and the poll cap throws rather than
 * spinning forever.
 */

vi.mock('@/lib/api/client', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() },
}));

const mockGet = vi.mocked(api.GET);

function row(status: string, extra: Record<string, unknown> = {}) {
  return {
    data: {
      job_id: 'j-1',
      kind: 'backfill',
      status,
      messages_listed: 10,
      outcomes_inserted: 2,
      error_message: null,
      ...extra,
    },
    error: undefined,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  mockGet.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('pollGmailJob', () => {
  test('polls until success and returns the final row', async () => {
    mockGet
      .mockResolvedValueOnce(row('running') as never)
      .mockResolvedValueOnce(row('running') as never)
      .mockResolvedValueOnce(row('success') as never);

    const promise = pollGmailJob('j-1', { intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(3000);
    const result = (await promise) as { status: string };

    expect(result.status).toBe('success');
    expect(mockGet).toHaveBeenCalledTimes(3);
    expect(mockGet).toHaveBeenCalledWith('/admin/gmail/jobs/{job_id}', {
      params: { path: { job_id: 'j-1' } },
    });
  });

  test('failed row throws with its error_message', async () => {
    mockGet.mockResolvedValueOnce(
      row('failed', { error_message: 'GMAIL_REFRESH_TOKEN revoked' }) as never,
    );

    const promise = pollGmailJob('j-1', { intervalMs: 1000 });
    // Attach the rejection handler BEFORE advancing timers so the rejection
    // is never unhandled.
    const assertion = expect(promise).rejects.toThrow('GMAIL_REFRESH_TOKEN revoked');
    await vi.advanceTimersByTimeAsync(1000);
    await assertion;
  });

  test('poll cap exhausts with a still-running job', async () => {
    mockGet.mockResolvedValue(row('running') as never);

    const promise = pollGmailJob('j-1', { intervalMs: 1000, maxPolls: 5 });
    const assertion = expect(promise).rejects.toThrow(/still running/);
    await vi.advanceTimersByTimeAsync(5000);
    await assertion;

    expect(mockGet).toHaveBeenCalledTimes(5);
  });
});
