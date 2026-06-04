import { describe, expect, test } from 'vitest';

import type { OutcomeEvent } from '@/lib/applied/types';
import { summarizeOutcomes } from '@/lib/companies/summaries';

let seq = 0;
// feat/applied-company-tracking: outcomes match a company by target_company_id
// (posting_id is uniformly NULL).
function outcome(targetCompanyId: string | null, stage: string): OutcomeEvent {
  seq += 1;
  return {
    id: `o-${seq}`,
    posting_id: null,
    target_company_id: targetCompanyId,
    received_at: new Date().toISOString(),
    stage,
    confidence: 0.9,
  };
}

describe('summarizeOutcomes', () => {
  test('returns — when the company has no linked outcomes', () => {
    expect(summarizeOutcomes('c1', [], [])).toBe('—');
  });

  test('ignores outcomes linked to a different company', () => {
    expect(summarizeOutcomes('c1', [], [outcome('c2', 'onsite_interview_invite')])).toBe('—');
  });

  test('returns "No response yet" when only an application_confirmation has landed', () => {
    expect(summarizeOutcomes('c1', [], [outcome('c1', 'application_confirmation')])).toBe(
      'No response yet',
    );
  });

  test('pluralizes counts naturally (matched by target_company_id)', () => {
    const result = summarizeOutcomes(
      'c1',
      [],
      [
        outcome('c1', 'recruiter_screen_invite'),
        outcome('c1', 'phone_interview_invite'),
        outcome('c1', 'onsite_interview_invite'),
      ],
    );
    expect(result).toBe('1 onsite, 2 screens');
  });

  test('singular form for count=1', () => {
    expect(summarizeOutcomes('c1', [], [outcome('c1', 'rejection_pre_screen')])).toBe(
      '1 rejection',
    );
  });
});
