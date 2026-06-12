import { describe, expect, test } from 'vitest';

import { buildStatsCsv } from '@/lib/stats/exportCsv';

describe('buildStatsCsv', () => {
  test('emits kpi rows then funnel rows as (section, metric, value)', () => {
    const csv = buildStatsCsv(
      [
        { metric: 'Applications (7d)', value: '12' },
        { metric: 'Response rate', value: '25%' },
      ],
      [
        { stage: 'Applied', count: 40 },
        { stage: 'Offer', count: 1 },
      ],
    );
    expect(csv.split('\r\n')).toEqual([
      'section,metric,value',
      'kpi,Applications (7d),12',
      'kpi,Response rate,25%',
      'funnel,Applied,40',
      'funnel,Offer,1',
    ]);
  });

  test('empty inputs → header only', () => {
    expect(buildStatsCsv([], [])).toBe('section,metric,value');
  });
});
