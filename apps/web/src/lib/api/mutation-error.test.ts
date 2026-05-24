import { describe, expect, test } from 'vitest';

import { MutationError, extractDetail } from '@/lib/api/mutation-error';

/**
 * Unit tests for the PR #58 typed error + detail extractor.
 *
 * No retry tests — the 5xx-retry helper was a misdirected fix for what
 * turned out to be a wire-shape bug (``kind`` vs ``action_type``).
 * See ``mutation-error.ts`` header for the post-mortem.
 */
describe('MutationError', () => {
  test('carries kind/status/detail/message on the instance', () => {
    const err = new MutationError({
      kind: 'application',
      status: 422,
      detail: 'wrong_role',
      message: 'wrong_role',
    });
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe('MutationError');
    expect(err.kind).toBe('application');
    expect(err.status).toBe(422);
    expect(err.detail).toBe('wrong_role');
    expect(err.message).toBe('wrong_role');
  });
});

describe('extractDetail', () => {
  test('null / undefined input → null', () => {
    expect(extractDetail(null)).toBeNull();
    expect(extractDetail(undefined)).toBeNull();
  });

  test('string input passes through', () => {
    expect(extractDetail('plain string')).toBe('plain string');
  });

  test('FastAPI HTTPException shape: {detail: "..."} → string', () => {
    expect(extractDetail({ detail: 'reason_required_for_not_interested' })).toBe(
      'reason_required_for_not_interested',
    );
  });

  test('FastAPI validation shape: {detail: [{msg, ...}]} → first msg', () => {
    const validationError = {
      detail: [
        {
          loc: ['body', 'action_type'],
          msg: 'field required',
          type: 'value_error.missing',
        },
        { loc: ['body', 'other'], msg: 'second message', type: 'whatever' },
      ],
    };
    expect(extractDetail(validationError)).toBe('field required');
  });

  test('object without detail key → null', () => {
    expect(extractDetail({ foo: 'bar' })).toBeNull();
  });

  test('detail array of unexpected shape → null', () => {
    expect(extractDetail({ detail: [{ noMsg: true }] })).toBeNull();
  });

  test('detail = numeric → null (not coerced)', () => {
    expect(extractDetail({ detail: 42 })).toBeNull();
  });
});
