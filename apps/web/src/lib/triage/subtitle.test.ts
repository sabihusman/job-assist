import { describe, expect, test } from 'vitest';

import { computeSubtitle } from '@/lib/triage/subtitle';

describe('computeSubtitle (PR #43)', () => {
  test("returns 'loading…' while the pending count is still loading", () => {
    expect(
      computeSubtitle({
        pendingTotal: null,
        appliedTotal: null,
        isPendingLoading: true,
        isError: false,
      }),
    ).toBe('loading…');
  });

  test('returns "{N} pending" when applied count is still loading', () => {
    expect(
      computeSubtitle({
        pendingTotal: 2080,
        appliedTotal: null,
        isPendingLoading: false,
        isError: false,
      }),
    ).toBe('2080 pending');
  });

  test('returns "{N} pending · {M} applied" when both queries have loaded', () => {
    expect(
      computeSubtitle({
        pendingTotal: 2080,
        appliedTotal: 5,
        isPendingLoading: false,
        isError: false,
      }),
    ).toBe('2080 pending · 5 applied');
  });

  test("falls back to 'Pending review' on error to preserve legacy behavior", () => {
    expect(
      computeSubtitle({
        pendingTotal: null,
        appliedTotal: null,
        isPendingLoading: false,
        isError: true,
      }),
    ).toBe('Pending review');
  });

  test('error wins even when data is partially loaded', () => {
    expect(
      computeSubtitle({
        pendingTotal: 2080,
        appliedTotal: 5,
        isPendingLoading: false,
        isError: true,
      }),
    ).toBe('Pending review');
  });

  test('handles zero counts as real values', () => {
    expect(
      computeSubtitle({
        pendingTotal: 0,
        appliedTotal: 0,
        isPendingLoading: false,
        isError: false,
      }),
    ).toBe('0 pending · 0 applied');
  });
});
