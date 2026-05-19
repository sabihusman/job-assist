import { describe, expect, test } from 'vitest';

import type { OutcomeEvent } from '@/lib/applied/types';
import { bucketPostings, emptyBuckets } from '@/lib/pipeline/bucket';
import type { PostingListItem } from '@/lib/triage/types';

function posting(id: string, opts: { appliedAt?: string; tier?: number } = {}): PostingListItem {
  const iso = opts.appliedAt ?? new Date().toISOString();
  return {
    id,
    company: {
      id: `c-${id}`,
      name: `Co${id}`,
      domain: null,
      description: null,
      tier: opts.tier ?? 1,
    },
    role: {
      title: 'PM',
      family: 'product_management',
      department: null,
      team: null,
      seniority: null,
    },
    location_raw: null,
    locations_normalized: [],
    remote_type: null,
    salary: null,
    source: { ats: 'greenhouse', url: null },
    first_seen_at: iso,
    score: null,
    state: {
      current: 'applied',
      reason: null,
      snooze_until: null,
      current_at: iso,
    },
  };
}

function outcome(postingId: string, stage: string, receivedIso: string): OutcomeEvent {
  return {
    id: `o-${postingId}-${stage}`,
    posting_id: postingId,
    received_at: receivedIso,
    stage,
    confidence: 0.9,
  };
}

describe('emptyBuckets', () => {
  test('returns all 8 stages with empty arrays', () => {
    const b = emptyBuckets();
    expect(Object.keys(b)).toHaveLength(8);
    expect(b.applied).toEqual([]);
    expect(b.ghosted).toEqual([]);
  });
});

describe('bucketPostings', () => {
  const NOW = Date.parse('2026-06-01T00:00:00Z');

  test('posting with no outcomes lands in APPLIED', () => {
    const p = posting('a', { appliedAt: '2026-05-28T00:00:00Z' });
    const b = bucketPostings([p], [], NOW);
    expect(b.applied.map((c) => c.id)).toEqual(['a']);
    expect(b.ghosted).toEqual([]);
  });

  test('latest outcome event wins when bucketing', () => {
    const p = posting('b', { appliedAt: '2026-05-01T00:00:00Z' });
    const outcomes = [
      outcome('b', 'recruiter_screen_invite', '2026-05-05T00:00:00Z'),
      outcome('b', 'phone_interview_invite', '2026-05-10T00:00:00Z'),
      outcome('b', 'onsite_interview_invite', '2026-05-20T00:00:00Z'),
    ];
    const b = bucketPostings([p], outcomes, NOW);
    expect(b.onsite.map((c) => c.id)).toEqual(['b']);
    expect(b.recruiter).toEqual([]);
    expect(b.phone).toEqual([]);
  });

  test('no outcomes + applied >30d ago → GHOSTED', () => {
    const p = posting('c', { appliedAt: '2026-04-01T00:00:00Z' });
    const b = bucketPostings([p], [], NOW);
    expect(b.ghosted.map((c) => c.id)).toEqual(['c']);
    expect(b.applied).toEqual([]);
  });

  test('unmapped outcome types fall back to APPLIED bucket', () => {
    const p = posting('d', { appliedAt: '2026-05-30T00:00:00Z' });
    const b = bucketPostings([p], [outcome('d', 'unclassified', '2026-05-31T00:00:00Z')], NOW);
    expect(b.applied.map((c) => c.id)).toEqual(['d']);
  });
});
