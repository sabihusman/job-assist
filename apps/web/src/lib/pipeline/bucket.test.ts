import { describe, expect, test } from 'vitest';

import type { OutcomeEvent } from '@/lib/applied/types';
import { bucketOutcomes, emptyBuckets } from '@/lib/pipeline/bucket';

describe('emptyBuckets', () => {
  test('returns all 8 stages with empty arrays', () => {
    const b = emptyBuckets();
    expect(Object.keys(b)).toHaveLength(8);
    expect(b.applied).toEqual([]);
    expect(b.ghosted).toEqual([]);
  });
});

function oe(partial: Partial<OutcomeEvent>): OutcomeEvent {
  return {
    id: 'o1',
    posting_id: null,
    received_at: '2026-01-01T00:00:00Z',
    stage: 'application_confirmation',
    confidence: null,
    company_name: null,
    subject: 'Applying to Acme',
    from_domain: 'acme.com',
    email_thread_id: 't1',
    target_company_id: null,
    ...partial,
  };
}

describe('bucketOutcomes — companyId carry (feat/repeat-signal-flags)', () => {
  test('carries target_company_id onto the card', () => {
    const b = bucketOutcomes([oe({ target_company_id: 'co-123' })]);
    expect(b.applied[0].companyId).toBe('co-123');
  });

  test('companyId is null for an unlinked outcome', () => {
    const b = bucketOutcomes([oe({ target_company_id: null })]);
    expect(b.applied[0].companyId).toBeNull();
  });

  test('picks the linked id from any row in the thread group', () => {
    const b = bucketOutcomes([
      oe({ id: 'a', target_company_id: null, received_at: '2026-01-01T00:00:00Z' }),
      oe({ id: 'b', target_company_id: 'co-9', received_at: '2026-01-02T00:00:00Z' }),
    ]); // same thread t1 → one card
    expect(b.applied).toHaveLength(1);
    expect(b.applied[0].companyId).toBe('co-9');
  });
});
