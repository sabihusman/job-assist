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
    // PR #43 added workday to the valid ATS set, so the bogus value
    // here is "bogus_ats". Anything not in the union gets stripped.
    const f = parseFilters(new URLSearchParams('ats=bogus_ats&ats=greenhouse'));
    expect(f.ats).toEqual(['greenhouse']);
  });

  test('PR #43: workday is now a valid ATS', () => {
    const f = parseFilters(new URLSearchParams('ats=workday'));
    expect(f.ats).toEqual(['workday']);
  });

  test('PR #55: icims is a valid ATS', () => {
    const f = parseFilters(new URLSearchParams('ats=icims'));
    expect(f.ats).toEqual(['icims']);
  });

  test('PR #43: other is now a valid role_family', () => {
    const f = parseFilters(new URLSearchParams('role_family=other'));
    expect(f.role_family).toEqual(['other']);
  });

  // ── PR #49: sort ──────────────────────────────────────────────────────

  test('PR #49: default sort is newest when no param', () => {
    const f = parseFilters(new URLSearchParams(''));
    expect(f.sort).toBe('newest');
  });

  test('PR #49: explicit sort param is honored', () => {
    const f = parseFilters(new URLSearchParams('sort=salary_high_to_low'));
    expect(f.sort).toBe('salary_high_to_low');
  });

  test('PR #49: unknown sort falls back to newest', () => {
    const f = parseFilters(new URLSearchParams('sort=salary_low_to_high'));
    expect(f.sort).toBe('newest');
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

  test('PR #49: omits the default sort from the URL', () => {
    const params = encodeFilters({ sort: 'newest' });
    expect(params.has('sort')).toBe(false);
  });

  test('PR #49: emits non-default sort', () => {
    const params = encodeFilters({ sort: 'tier' });
    expect(params.get('sort')).toBe('tier');
  });
});

describe('toggleInArray', () => {
  test('adds when absent, removes when present', () => {
    expect(toggleInArray([1, 2], 3)).toEqual([1, 2, 3]);
    expect(toggleInArray([1, 2, 3], 2)).toEqual([1, 3]);
  });
});
