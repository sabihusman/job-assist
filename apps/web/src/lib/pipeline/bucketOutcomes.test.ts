import { describe, expect, test } from 'vitest';

import type { OutcomeEvent } from '@/lib/applied/types';
import { bucketOutcomes } from '@/lib/pipeline/bucket';

let seq = 0;
function oc(partial: Partial<OutcomeEvent> & { stage: string }): OutcomeEvent {
  seq += 1;
  return {
    id: partial.id ?? `o-${seq}`,
    posting_id: partial.posting_id ?? null,
    received_at: partial.received_at ?? '2026-05-01T00:00:00Z',
    stage: partial.stage,
    confidence: 0.9,
    company_name: partial.company_name ?? null,
    subject: partial.subject ?? 'Thank you for applying to Acme',
    from_domain: partial.from_domain ?? 'greenhouse.io',
    email_thread_id: partial.email_thread_id ?? null,
  };
}

describe('bucketOutcomes', () => {
  test('N application_confirmation outcomes produce N cards in Applied', () => {
    const outcomes = Array.from({ length: 5 }, (_, i) =>
      oc({ stage: 'application_confirmation', id: `c-${i}`, email_thread_id: `t-${i}` }),
    );
    const b = bucketOutcomes(outcomes);
    expect(b.applied).toHaveLength(5);
    expect(b.rejected).toHaveLength(0);
  });

  test('a rejection sharing a thread_id with a confirmation moves the card to Rejected (latest-wins)', () => {
    const b = bucketOutcomes([
      oc({
        stage: 'application_confirmation',
        email_thread_id: 'thread-1',
        received_at: '2026-05-01T00:00:00Z',
        subject: 'Thank you for applying to Ramp',
      }),
      oc({
        stage: 'rejection_post_screen',
        email_thread_id: 'thread-1',
        received_at: '2026-05-10T00:00:00Z',
        subject: 'Update on your Ramp application',
      }),
    ]);
    // One grouped card (single thread), landed in Rejected by the later event.
    expect(b.applied).toHaveLength(0);
    expect(b.rejected).toHaveLength(1);
    expect(b.rejected[0].companyName).toBe('Ramp'); // extracted from the confirmation's subject, not the vague rejection subject
  });

  test('an UNLINKED outcome (target_company + posting both null) still renders, labelled from subject', () => {
    const b = bucketOutcomes([
      oc({
        stage: 'application_confirmation',
        posting_id: null,
        company_name: null,
        subject: 'Thank you for applying to Uphold!',
        from_domain: 'ashbyhq.com',
        email_thread_id: 't-x',
      }),
    ]);
    expect(b.applied).toHaveLength(1);
    // Subject-derived label, NOT the ATS from_domain.
    expect(b.applied[0].companyName).toBe('Uphold');
  });

  test('label falls back to from_domain then is never the literal when subject is generic', () => {
    const b = bucketOutcomes([
      oc({
        stage: 'application_confirmation',
        company_name: null,
        subject: 'Update on Your Application',
        from_domain: 'jobs.lever.co',
        email_thread_id: 't-y',
      }),
    ]);
    // company_name null + subject not extractable → from_domain.
    expect(b.applied[0].companyName).toBe('jobs.lever.co');
  });

  test('a linked company_name wins over subject extraction', () => {
    const b = bucketOutcomes([
      oc({
        stage: 'recruiter_screen_invite',
        company_name: 'Plaid Inc.',
        subject: 'Thank you for applying to Plaid',
        email_thread_id: 't-z',
      }),
    ]);
    expect(b.recruiter).toHaveLength(1);
    expect(b.recruiter[0].companyName).toBe('Plaid Inc.');
  });

  test('unrelated / unclassified outcomes produce zero cards', () => {
    const b = bucketOutcomes([
      oc({ stage: 'unrelated', email_thread_id: 't1' }),
      oc({ stage: 'unclassified', email_thread_id: 't2' }),
    ]);
    const total = Object.values(b).reduce((n, cards) => n + cards.length, 0);
    expect(total).toBe(0);
  });

  test('rows without a thread_id are bucketed per-event', () => {
    const b = bucketOutcomes([
      oc({ stage: 'application_confirmation', email_thread_id: null, id: 'a' }),
      oc({ stage: 'application_confirmation', email_thread_id: null, id: 'b' }),
    ]);
    expect(b.applied).toHaveLength(2);
  });
});
