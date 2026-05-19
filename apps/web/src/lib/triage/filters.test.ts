import { describe, expect, test } from 'vitest';

import { encodeFilters, parseFilters, toggleInArray } from '@/lib/triage/filters';

describe('parseFilters', () => {
  test('defaults state to [triage] when no state param is set', () => {
    const f = parseFilters(new URLSearchParams(''));
    expect(f.state).toEqual(['triage']);
    expect(f.tier).toEqual([]);
  });

  test('coerces tier param to integers', () => {
    const f = parseFilters(new URLSearchParams('tier=1&tier=2'));
    expect(f.tier).toEqual([1, 2]);
  });

  test('honors explicit state param', () => {
    const f = parseFilters(new URLSearchParams('state=snoozed&include_snoozed_past_only=true'));
    expect(f.state).toEqual(['snoozed']);
    expect(f.include_snoozed_past_only).toBe(true);
  });

  test('drops invalid enum values', () => {
    const f = parseFilters(new URLSearchParams('ats=workday&ats=greenhouse'));
    expect(f.ats).toEqual(['greenhouse']);
  });
});

describe('encodeFilters', () => {
  test('round-trips a basic filter set', () => {
    const params = encodeFilters({
      tier: [1, 2],
      ats: ['greenhouse'],
      remote_type: ['remote'],
      role_family: [],
      state: ['triage'],
      include_snoozed_past_only: false,
    });
    expect(params.getAll('tier')).toEqual(['1', '2']);
    expect(params.getAll('ats')).toEqual(['greenhouse']);
    expect(params.has('include_snoozed_past_only')).toBe(false);
  });

  test('emits include_snoozed_past_only=true when set', () => {
    const params = encodeFilters({ include_snoozed_past_only: true });
    expect(params.get('include_snoozed_past_only')).toBe('true');
  });
});

describe('toggleInArray', () => {
  test('adds when absent, removes when present', () => {
    expect(toggleInArray([1, 2], 3)).toEqual([1, 2, 3]);
    expect(toggleInArray([1, 2, 3], 2)).toEqual([1, 3]);
  });
});
