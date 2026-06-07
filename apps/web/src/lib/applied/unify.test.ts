import { describe, expect, it } from 'vitest';

import type { OutcomeEvent, ResolvedStatus } from '@/lib/applied/types';
import { entryStage, unifyApplied } from '@/lib/applied/unify';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Pure tests for the unified Applied resolution (feat/applied-unified).
 * These lock the three invariants the operator asked for:
 *   1. The ~150 Gmail applications all surface (membership = Pipeline).
 *   2. Manual application_state is AUTHORITATIVE where set (overlay wins).
 *   3. No company-level fanout — a Gmail row only touches the posting it was
 *      SPECIFICALLY linked to via job_posting_id (#162); unlinked rows never
 *      borrow a manual status from a sibling posting at the same company.
 */

let seq = 0;
function outcome(partial: Partial<OutcomeEvent> = {}): OutcomeEvent {
  seq += 1;
  return {
    id: partial.id ?? `evt-${seq}`,
    posting_id: partial.posting_id ?? null,
    received_at: partial.received_at ?? '2026-01-01T00:00:00Z',
    stage: partial.stage ?? 'application_confirmation',
    confidence: partial.confidence ?? 0.9,
    company_name: partial.company_name ?? null,
    subject: partial.subject ?? 'Thanks for applying',
    from_domain: partial.from_domain ?? 'greenhouse.io',
    email_thread_id: partial.email_thread_id ?? null,
    target_company_id: partial.target_company_id ?? null,
    raw_snippet: partial.raw_snippet ?? null,
    posting_title: partial.posting_title ?? null,
    manual_status: partial.manual_status ?? null,
  };
}

function posting(
  id: string,
  status: ResolvedStatus,
  opts: { company?: string; role?: string; tier?: number } = {},
): PostingListItem {
  return {
    id,
    company: {
      id: `c-${id}`,
      name: opts.company ?? 'Acme',
      domain: null,
      description: null,
      tier: opts.tier ?? 2,
    },
    role: {
      title: opts.role ?? 'Product Manager',
      family: null,
      department: null,
      team: null,
      seniority: null,
    },
    location_raw: null,
    locations_normalized: [],
    remote_type: null,
    salary: null,
    source: { ats: 'greenhouse', url: null },
    first_seen_at: '2025-12-01T00:00:00Z',
    score: null,
    state: {
      current: 'applied',
      reason: null,
      snooze_until: null,
      current_at: '2025-12-15T00:00:00Z',
      resolved_status: status,
    },
  };
}

describe('unifyApplied', () => {
  it('surfaces every Gmail application, including direct apps with no corpus posting', () => {
    const outcomes = [
      outcome({ email_thread_id: 't1', company_name: 'Stripe' }),
      outcome({ email_thread_id: 't2', company_name: 'Plaid' }),
      outcome({ email_thread_id: 't3', company_name: 'Brex' }), // direct app, no posting_id
    ];
    const entries = unifyApplied(outcomes, []);
    expect(entries).toHaveLength(3);
    expect(entries.every((e) => e.source === 'gmail')).toBe(true);
    expect(entries.map((e) => e.company).sort()).toEqual(['Brex', 'Plaid', 'Stripe']);
  });

  it('collapses a multi-email thread into ONE entry (latest stage wins)', () => {
    const outcomes = [
      outcome({
        email_thread_id: 't1',
        stage: 'application_confirmation',
        received_at: '2026-01-01T00:00:00Z',
      }),
      outcome({
        email_thread_id: 't1',
        stage: 'rejection_post_screen',
        received_at: '2026-02-01T00:00:00Z',
      }),
    ];
    const entries = unifyApplied(outcomes, []);
    expect(entries).toHaveLength(1);
    expect(entryStage(entries[0])).toBe('rejected'); // latest-wins
    expect(entries[0].events).toHaveLength(2);
  });

  it('manual status is authoritative over the Gmail stage when both exist (source=both)', () => {
    // Gmail says rejected; the operator manually marked it "offer" → manual wins.
    const outcomes = [
      outcome({ email_thread_id: 't1', posting_id: 'p1', stage: 'rejection_post_screen' }),
    ];
    const manual = [posting('p1', 'offer', { company: 'Stripe' })];
    const entries = unifyApplied(outcomes, manual);
    expect(entries).toHaveLength(1);
    expect(entries[0].source).toBe('both');
    expect(entries[0].manualStatus).toBe('offer');
    expect(entryStage(entries[0])).toBe('offer'); // not 'rejected'
    expect(entries[0].postingId).toBe('p1');
    expect(entries[0].company).toBe('Stripe');
  });

  it('dedupes a manual posting and its linked Gmail thread into one entry', () => {
    const outcomes = [outcome({ email_thread_id: 't1', posting_id: 'p1' })];
    const manual = [posting('p1', 'applied')];
    const entries = unifyApplied(outcomes, manual);
    expect(entries).toHaveLength(1); // NOT two
    expect(entries[0].key).toBe('posting:p1');
  });

  it('collapses two Gmail threads linked to the SAME posting into one entry', () => {
    const outcomes = [
      outcome({ email_thread_id: 't1', posting_id: 'p1', stage: 'application_confirmation' }),
      outcome({
        email_thread_id: 't2',
        posting_id: 'p1',
        stage: 'recruiter_screen_invite',
        received_at: '2026-03-01T00:00:00Z',
      }),
    ];
    const entries = unifyApplied(outcomes, []);
    expect(entries).toHaveLength(1);
    expect(entries[0].key).toBe('posting:p1');
    expect(entries[0].events).toHaveLength(2);
  });

  it('NO FANOUT: a manual status never bleeds onto an unlinked Gmail row at the same company', () => {
    // p1 is manually "offer". A separate Gmail thread at the same company is
    // NOT linked to p1 (posting_id null). It must stay a plain gmail entry —
    // the offer must NOT fan out onto it.
    const outcomes = [
      outcome({ email_thread_id: 't1', posting_id: 'p1', company_name: 'Stripe' }),
      outcome({ email_thread_id: 't2', posting_id: null, company_name: 'Stripe' }),
    ];
    const manual = [posting('p1', 'offer', { company: 'Stripe' })];
    const entries = unifyApplied(outcomes, manual);
    expect(entries).toHaveLength(2);
    const linked = entries.find((e) => e.postingId === 'p1');
    const unlinked = entries.find((e) => e.postingId === null);
    expect(linked?.manualStatus).toBe('offer');
    expect(unlinked?.manualStatus).toBeNull(); // the guard
    expect(unlinked?.source).toBe('gmail');
  });

  it('emits a manual-only entry when a manual application has no Gmail thread', () => {
    const manual = [posting('p9', 'interview', { company: 'Figma' })];
    const entries = unifyApplied([], manual);
    expect(entries).toHaveLength(1);
    expect(entries[0].source).toBe('manual');
    expect(entries[0].manualStatus).toBe('interview');
    expect(entries[0].events).toHaveLength(0);
    expect(entryStage(entries[0])).toBe('video'); // interview → mid-funnel
  });

  it('uses the /outcomes manual_status join for terminal statuses missing from the active funnel', () => {
    // A manually-REJECTED posting is excluded from GET /postings?state=applied,
    // so it is absent from manualPostings — but the linked Gmail row carries
    // manual_status via the join, so the overlay still applies.
    const outcomes = [
      outcome({
        email_thread_id: 't1',
        posting_id: 'p1',
        stage: 'application_confirmation',
        manual_status: 'rejected',
      }),
    ];
    const entries = unifyApplied(outcomes, []);
    expect(entries).toHaveLength(1);
    expect(entries[0].source).toBe('both');
    expect(entries[0].manualStatus).toBe('rejected');
    expect(entryStage(entries[0])).toBe('rejected');
  });

  it('prefers the real posting title (from the #162 link) for the role label', () => {
    const outcomes = [
      outcome({
        email_thread_id: 't1',
        posting_id: 'p1',
        posting_title: 'Product Manager, Risk',
        subject: 'Thanks for applying',
      }),
    ];
    const entries = unifyApplied(outcomes, []);
    expect(entries[0].role).toBe('Product Manager, Risk');
  });
});
