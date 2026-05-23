import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { RejectedRow } from '@/components/rejected/RejectedRow';
import type { PostingListItem } from '@/lib/triage/types';

function makePosting(overrides: Partial<PostingListItem> = {}): PostingListItem {
  return {
    id: 'p-1',
    company: { id: 'c-1', name: 'Globex', domain: null, description: null, tier: 2 },
    role: {
      title: 'Lead PM, Growth',
      family: 'product_management',
      department: null,
      team: null,
      seniority: 'lead_pm',
    },
    location_raw: 'SF',
    locations_normalized: ['SF'],
    remote_type: 'hybrid',
    salary: null,
    source: { ats: 'lever', url: 'https://example.test/jd' },
    first_seen_at: new Date('2026-04-01T00:00:00Z').toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
    ...overrides,
  };
}

describe('RejectedRow', () => {
  test('renders company name and role title', () => {
    render(
      <ul>
        <RejectedRow posting={makePosting()} />
      </ul>,
    );
    expect(screen.getByText('Globex')).toBeInTheDocument();
    expect(screen.getByText('Lead PM, Growth')).toBeInTheDocument();
  });

  test('renders tier badge (T2 in this fixture)', () => {
    render(
      <ul>
        <RejectedRow posting={makePosting()} />
      </ul>,
    );
    expect(screen.getByLabelText('Tier 2')).toBeInTheDocument();
  });

  test('renders "posted" date label', () => {
    render(
      <ul>
        <RejectedRow posting={makePosting()} />
      </ul>,
    );
    expect(screen.getByText(/posted/i)).toBeInTheDocument();
  });

  test('falls back to tier 4 styling when company.tier is null', () => {
    const p = makePosting({
      company: { id: 'c-1', name: 'NoTier Co', domain: null, description: null, tier: null },
    });
    render(
      <ul>
        <RejectedRow posting={p} />
      </ul>,
    );
    expect(screen.getByLabelText('Tier 4')).toBeInTheDocument();
  });
});
