import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { PassedRow } from '@/components/passed/PassedRow';
import type { PostingListItem } from '@/lib/triage/types';

function makePosting(overrides: Partial<PostingListItem> = {}): PostingListItem {
  return {
    id: 'p-1',
    company: { id: 'c-1', name: 'Acme Co', domain: null, description: null, tier: 1 },
    role: {
      title: 'Senior PM, Platform',
      family: 'product_management',
      department: null,
      team: null,
      seniority: 'senior_pm',
    },
    location_raw: 'Remote',
    locations_normalized: ['Remote'],
    remote_type: 'remote',
    salary: null,
    source: { ats: 'greenhouse', url: 'https://example.test/jd' },
    first_seen_at: new Date('2026-05-10T00:00:00Z').toISOString(),
    score: null,
    state: {
      current: 'not_interested',
      reason: 'too_senior',
      snooze_until: null,
      current_at: new Date('2026-05-15T00:00:00Z').toISOString(),
    },
    ...overrides,
  };
}

describe('PassedRow', () => {
  test('renders company name, role title, and tier badge', () => {
    render(
      <ul>
        <PassedRow posting={makePosting()} />
      </ul>,
    );
    expect(screen.getByText('Acme Co')).toBeInTheDocument();
    expect(screen.getByText('Senior PM, Platform')).toBeInTheDocument();
    expect(screen.getByLabelText('Tier 1')).toBeInTheDocument();
  });

  test('surfaces the pass reason inline as a chip', () => {
    render(
      <ul>
        <PassedRow
          posting={makePosting({ state: { ...makePosting().state, reason: 'wrong_role' } })}
        />
      </ul>,
    );
    // Reason chip uses the same label vocabulary as ReasonPicker.
    expect(screen.getByLabelText('Reason: Wrong role')).toBeInTheDocument();
    expect(screen.getByText('Wrong role')).toBeInTheDocument();
  });

  test('renders without a reason chip when state.reason is null', () => {
    const p = makePosting({
      state: {
        current: 'not_interested',
        reason: null,
        snooze_until: null,
        current_at: new Date().toISOString(),
      },
    });
    render(
      <ul>
        <PassedRow posting={p} />
      </ul>,
    );
    // No "Reason: …" aria-label present.
    expect(screen.queryByLabelText(/^Reason:/)).toBeNull();
  });

  test('renders "passed" date label', () => {
    render(
      <ul>
        <PassedRow posting={makePosting()} />
      </ul>,
    );
    expect(screen.getByText(/passed/i)).toBeInTheDocument();
  });
});
