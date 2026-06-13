import { describe, expect, test } from 'vitest';

import {
  encodeFilters,
  parseFilters,
  resolveRoleFamilies,
  toggleInArray,
} from '@/lib/triage/filters';

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

  test('feat/wellfound-cron-health: wellfound is a valid ATS', () => {
    const f = parseFilters(new URLSearchParams('ats=wellfound'));
    expect(f.ats).toEqual(['wellfound']);
  });

  test('PR #43: other is now a valid role_family', () => {
    const f = parseFilters(new URLSearchParams('role_family=other'));
    expect(f.role_family).toEqual(['other']);
  });

  // feat/strategy-spine regression: strategy_ops must survive parseFilters
  // (VALID_FAMILY allowlist). Without it the Strategy/Ops chip is a no-op —
  // parse strips the param and the queue falls back to pm_only PM/PO.
  test('preserves strategy_ops (the Strategy/Ops chip is a real selection)', () => {
    const f = parseFilters(new URLSearchParams('role_family=strategy_ops'));
    expect(f.role_family).toEqual(['strategy_ops']);
    expect(encodeFilters(f).getAll('role_family')).toEqual(['strategy_ops']);
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

  test('PR #57: best_fit is a valid sort key', () => {
    const f = parseFilters(new URLSearchParams('sort=best_fit'));
    expect(f.sort).toBe('best_fit');
  });

  test('Slice 2b: best_fit_semantic is a valid sort key', () => {
    const f = parseFilters(new URLSearchParams('sort=best_fit_semantic'));
    expect(f.sort).toBe('best_fit_semantic');
  });

  // ── pm_only (feat/pm-po-only-filter) ──────────────────────────────────

  test('pm_only defaults ON when the param is absent', () => {
    const f = parseFilters(new URLSearchParams(''));
    expect(f.pm_only).toBe(true);
  });

  test('pm_only=false turns the gate off', () => {
    const f = parseFilters(new URLSearchParams('pm_only=false'));
    expect(f.pm_only).toBe(false);
  });

  test('any non-false pm_only value is treated as on', () => {
    const f = parseFilters(new URLSearchParams('pm_only=true'));
    expect(f.pm_only).toBe(true);
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

  test('pm_only: omits the param when on (the default)', () => {
    const params = encodeFilters({ pm_only: true });
    expect(params.has('pm_only')).toBe(false);
  });

  test('pm_only: emits pm_only=false when toggled off', () => {
    const params = encodeFilters({ pm_only: false });
    expect(params.get('pm_only')).toBe('false');
  });
});

describe('resolveRoleFamilies', () => {
  // feat/strategy-spine: the no-leak guarantee. The default PM queue
  // (pm_only) must never include strategy_ops; the Strategy chip is an
  // explicit selection that overrides the gate.
  test('pm_only default EXCLUDES strategy_ops (no leak into the PM queue)', () => {
    const resolved = resolveRoleFamilies({ role_family: [], pm_only: true });
    expect(resolved).not.toContain('strategy_ops');
    expect(resolved).toEqual(['product_management', 'product_owner']);
  });

  test('explicit strategy_ops chip resolves to the strategy view', () => {
    expect(resolveRoleFamilies({ role_family: ['strategy_ops'], pm_only: true })).toEqual([
      'strategy_ops',
    ]);
  });

  test('default-on pm_only with no chips → PM + PO', () => {
    expect(resolveRoleFamilies({ role_family: [], pm_only: true })).toEqual([
      'product_management',
      'product_owner',
    ]);
  });

  test('gate off with no chips → all families (empty)', () => {
    expect(resolveRoleFamilies({ role_family: [], pm_only: false })).toEqual([]);
  });

  test('explicit family chips override the gate', () => {
    expect(resolveRoleFamilies({ role_family: ['product_marketing'], pm_only: true })).toEqual([
      'product_marketing',
    ]);
  });

  test('missing pm_only is treated as on', () => {
    expect(resolveRoleFamilies({ role_family: [] })).toEqual([
      'product_management',
      'product_owner',
    ]);
  });
});

describe('toggleInArray', () => {
  test('adds when absent, removes when present', () => {
    expect(toggleInArray([1, 2], 3)).toEqual([1, 2, 3]);
    expect(toggleInArray([1, 2, 3], 2)).toEqual([1, 3]);
  });
});
