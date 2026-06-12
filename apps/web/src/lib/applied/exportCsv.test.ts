import { describe, expect, test } from 'vitest';

import { buildUnifiedCsv } from '@/lib/applied/exportCsv';
import type { UnifiedAppliedEntry } from '@/lib/applied/unify';

function entry(overrides: Partial<UnifiedAppliedEntry> = {}): UnifiedAppliedEntry {
  return {
    key: 'posting:p1',
    company: 'Acme',
    role: 'Senior PM',
    postingId: 'p1',
    source: 'both',
    manualStatus: null,
    gmailStage: 'applied',
    at: Date.UTC(2026, 5, 1, 12, 0, 0),
    events: [],
    tier: 1,
    ...overrides,
  };
}

describe('buildUnifiedCsv (Applied/Rejected current-view export)', () => {
  test('serializes rows in given order with header', () => {
    const csv = buildUnifiedCsv([
      entry(),
      entry({ key: 'o:2', company: 'Beta, Co', role: null, source: 'gmail' }),
    ]);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe('source,company,role,status,last_activity,emails');
    // 'Still Alive' is STAGE_LABELS.applied — the export mirrors the
    // on-screen pill exactly, not a re-invented vocabulary.
    expect(lines[1]).toBe('both,Acme,Senior PM,Still Alive,2026-06-01,0');
    // Comma-bearing company is quoted; null role renders empty.
    expect(lines[2]).toBe('gmail,"Beta, Co",,Still Alive,2026-06-01,0');
  });

  test('manual status is authoritative over the Gmail stage — same as the pill', () => {
    const csv = buildUnifiedCsv([
      entry({ manualStatus: 'rejected', gmailStage: 'onsite' }),
      entry({ key: 'o:3', manualStatus: null, gmailStage: 'offer' }),
    ]);
    const lines = csv.split('\r\n');
    expect(lines[1]).toContain(',Rejected,');
    expect(lines[2]).toContain(',Offer,');
  });

  test('email count comes from the entry events', () => {
    const events = [{ id: 'e1' }, { id: 'e2' }] as unknown as UnifiedAppliedEntry['events'];
    const csv = buildUnifiedCsv([entry({ events })]);
    expect(csv.split('\r\n')[1]).toMatch(/,2$/);
  });

  test('empty view → header only', () => {
    expect(buildUnifiedCsv([])).toBe('source,company,role,status,last_activity,emails');
  });
});
