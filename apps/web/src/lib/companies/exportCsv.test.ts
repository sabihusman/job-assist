import { describe, expect, test } from 'vitest';

import { buildCompaniesCsv } from '@/lib/companies/exportCsv';
import type { CompanyListItem } from '@/lib/companies/types';

function company(overrides: Partial<CompanyListItem> = {}): CompanyListItem {
  return {
    id: 'c1',
    name: 'Acme',
    domain: 'acme.com',
    description: null,
    tier: 1,
    ats_set: ['greenhouse'],
    active_postings: 4,
    total_postings: 10,
    ats: 'greenhouse',
    ats_handle: 'acme',
    notes: null,
    source: 'curated',
    application_count: 2,
    last_applied_at: '2026-06-01T12:00:00Z',
    ...overrides,
  };
}

describe('buildCompaniesCsv', () => {
  test('mirrors the visible table columns', () => {
    const csv = buildCompaniesCsv([company()]);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe(
      'name,tier,ats,source,active_postings,total_postings,applications,last_applied,notes',
    );
    expect(lines[1]).toBe('Acme,1,greenhouse,curated,4,10,2,2026-06-01,');
  });

  test('NULL tier (broad shell) renders empty; multiple ats join; optional fields default', () => {
    const csv = buildCompaniesCsv([
      company({
        tier: null,
        ats_set: ['workday', 'icims'],
        source: undefined,
        application_count: undefined,
        last_applied_at: null,
        notes: 'board blocks egress, on Apify',
      }),
    ]);
    expect(csv.split('\r\n')[1]).toBe(
      'Acme,,workday; icims,,4,10,0,,"board blocks egress, on Apify"',
    );
  });

  test('falls back to the single ats field when ats_set is empty', () => {
    const csv = buildCompaniesCsv([company({ ats_set: [], ats: 'lever' })]);
    expect(csv.split('\r\n')[1]).toContain(',lever,');
  });
});
