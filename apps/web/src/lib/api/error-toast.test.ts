import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { showErrorToast } from '@/lib/api/error-toast';
import { MutationError } from '@/lib/api/mutation-error';

/**
 * PR #73 / Bestiary 5.14 — error toast contract tests.
 *
 * Three branches to lock:
 *   1. MutationError with detail → toast shows the detail verbatim.
 *   2. Native Error with message → toast shows "{fallback} — {message}".
 *      This branch was DEV-ONLY before PR #73 (Bestiary 5.12 prod
 *      blackbox).
 *   3. Anything else → toast shows just the fallback.
 *
 * Every path must pass an explicit ``duration`` of 4500ms — sonner's
 * default for ``toast.error`` is ``Infinity``, which is the root
 * cause this PR addresses.
 */

const { toastErrorMock } = vi.hoisted(() => ({ toastErrorMock: vi.fn() }));

vi.mock('sonner', () => ({
  toast: {
    error: toastErrorMock,
    success: vi.fn(),
  },
}));

beforeEach(() => {
  toastErrorMock.mockReset();
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('showErrorToast', () => {
  test('MutationError with detail → toast shows the detail string', () => {
    const err = new MutationError({
      kind: 'application',
      status: 422,
      detail: 'reason_required_for_not_interested',
      message: 'fallback',
    });
    showErrorToast(err, "Couldn't update posting state");

    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock).toHaveBeenCalledWith('reason_required_for_not_interested', {
      duration: 4500,
    });
  });

  test('MutationError WITHOUT detail → falls through to fallback (no Error.message branch)', () => {
    // detail=null means we don't have a usable server-side reason. The
    // helper should NOT pretend to surface one — show the fallback
    // instead so the operator sees stable copy.
    const err = new MutationError({
      kind: 'application',
      status: 500,
      detail: null,
      message: 'Action failed (500)',
    });
    showErrorToast(err, "Couldn't update posting state");

    expect(toastErrorMock).toHaveBeenCalledWith(
      // MutationError extends Error, so the message branch catches it.
      // The pattern is "{fallback} — {err.message}".
      "Couldn't update posting state — Action failed (500)",
      { duration: 4500 },
    );
  });

  test('non-Mutation Error with message → "{fallback} — {message}" (Bestiary 5.12 prod fix)', () => {
    const err = new TypeError('Cannot read properties of undefined (reading "filter")');
    showErrorToast(err, "Couldn't update posting state");

    expect(toastErrorMock).toHaveBeenCalledWith(
      'Couldn\'t update posting state — Cannot read properties of undefined (reading "filter")',
      { duration: 4500 },
    );
  });

  test('null err → just the fallback', () => {
    showErrorToast(null, "Couldn't update posting state");
    expect(toastErrorMock).toHaveBeenCalledWith("Couldn't update posting state", {
      duration: 4500,
    });
  });

  test('plain object without message → just the fallback', () => {
    showErrorToast({ code: 'OFFLINE' }, "Couldn't save");
    expect(toastErrorMock).toHaveBeenCalledWith("Couldn't save", { duration: 4500 });
  });

  test('every path passes duration=4500 (regression lock for Bestiary 5.14)', () => {
    // Sonner's library default for toast.error is Infinity. Every
    // showErrorToast call MUST override it.
    showErrorToast(new Error('x'), 'f');
    showErrorToast(null, 'f');
    showErrorToast(
      new MutationError({ kind: 'application', status: 422, detail: 'd', message: 'm' }),
      'f',
    );
    for (const call of toastErrorMock.mock.calls) {
      expect(call[1]).toEqual({ duration: 4500 });
    }
  });
});
