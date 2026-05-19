import { describe, expect, test } from 'vitest';

import type { OutcomeEvent } from '@/lib/applied/types';
import { countAppliedByCompany, summarizeOutcomes } from '@/lib/companies/summaries';
import type { PostingListItem } from '@/lib/triage/types';

function posting(id: string, companyId: string): PostingListItem {
  const iso = new Date().toISOString();
  return {
    id,
    company: { id: companyId, name: companyId, domain: null, description: null, tier: 1 },
    role: { title: 'PM', family: null, department: null, team: null, seniority: null },
    location_raw: null,
    locations_normalized: [],
    remote_type: null,
    salary: null,
    source: { ats: 'greenhouse', url: null },
    first_seen_at: iso,
    score: null,
    state: { current: 'applied', reason: null, snooze_until: null, current_at: iso },
  };
}

function outcome(postingId: string, stage: string): OutcomeEvent {
  return {
    id: `o-${postingId}-${stage}`,
    posting_id: postingId,
    received_at: new Date().toISOString(),
    stage,
    confidence: 0.9,
  };
}

describe('summarizeOutcomes', () => {
  test('returns — when company has no applied postings', () => {
    expect(summarizeOutcomes('c1', [], [])).toBe('—');
  });

  test('returns "No response yet" when applied count > 0 but no outcomes', () => {
    expect(summarizeOutcomes('c1', [posting('p1', 'c1')], [])).toBe('No response yet');
  });

  test('pluralizes counts naturally', () => {
    const result = summarizeOutcomes(
      'c1',
      [posting('p1', 'c1'), posting('p2', 'c1')],
      [
        outcome('p1', 'recruiter_screen_invite'),
        outcome('p1', 'phone_interview_invite'),
        outcome('p2', 'onsite_interview_invite'),
      ],
    );
    expect(result).toBe('1 onsite, 2 screens');
  });

  test('singular form for count=1', () => {
    const result = summarizeOutcomes(
      'c1',
      [posting('p1', 'c1')],
      [outcome('p1', 'rejection_pre_screen')],
    );
    expect(result).toBe('1 rejection');
  });
});

describe('countAppliedByCompany', () => {
  test('counts unique applied postings per company', () => {
    const counts = countAppliedByCompany([
      posting('p1', 'a'),
      posting('p2', 'a'),
      posting('p3', 'b'),
    ]);
    expect(counts.get('a')).toBe(2);
    expect(counts.get('b')).toBe(1);
    expect(counts.get('missing')).toBeUndefined();
  });
});
