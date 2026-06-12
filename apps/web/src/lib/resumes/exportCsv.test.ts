import { describe, expect, test } from 'vitest';

import type { ResumeAnalytics, ResumeVersion } from '@/lib/api/resume';
import { buildResumesCsv } from '@/lib/resumes/exportCsv';

const versions: ResumeVersion[] = [
  {
    id: 'v1',
    label: 'fintech-pm',
    angle: 'payments depth',
    snapshot_text: null,
    notes: 'lead with Stripe project',
    created_at: '2026-05-20T10:00:00Z',
  },
  {
    id: 'v2',
    label: 'platform-pm',
    angle: null,
    snapshot_text: null,
    notes: null,
    created_at: '2026-06-01T10:00:00Z',
  },
];

const analytics: ResumeAnalytics = {
  by_version: [
    {
      resume_version_id: 'v1',
      label: 'fintech-pm',
      angle: 'payments depth',
      applications: 7,
      companies: 6,
      companies_rejected: 2,
      companies_confirmed: 5,
    },
  ],
  funnel: [],
  ambiguous_companies: [],
  attribution_note: 'company-level',
};

describe('buildResumesCsv', () => {
  test('joins versions with their analytics row by id', () => {
    const csv = buildResumesCsv(versions, analytics);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe(
      'label,angle,applications,companies,companies_rejected,companies_confirmed,notes,created',
    );
    expect(lines[1]).toBe('fintech-pm,payments depth,7,6,2,5,lead with Stripe project,2026-05-20');
  });

  test('versions without an analytics row export with zero counts', () => {
    const csv = buildResumesCsv(versions, analytics);
    expect(csv.split('\r\n')[2]).toBe('platform-pm,,0,0,0,0,,2026-06-01');
  });

  test('undefined analytics (still loading / errored) zeroes every version', () => {
    const csv = buildResumesCsv(versions, undefined);
    expect(csv.split('\r\n')[1]).toBe(
      'fintech-pm,payments depth,0,0,0,0,lead with Stripe project,2026-05-20',
    );
  });
});
