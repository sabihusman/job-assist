import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { UnifiedRejectedList } from '@/components/rejected/UnifiedRejectedList';
import { useAllOutcomes } from '@/lib/api/applied';
import type { OutcomeEvent } from '@/lib/applied/types';
import type { PostingListItem } from '@/lib/triage/types';

vi.mock('@/lib/api/applied', () => ({ useAllOutcomes: vi.fn() }));
const mockOutcomes = vi.mocked(useAllOutcomes);

function setOutcomes(items: OutcomeEvent[]) {
  mockOutcomes.mockReturnValue({
    data: { total: items.length, offset: 0, limit: items.length, items },
  } as ReturnType<typeof useAllOutcomes>);
}

function oe(partial: Partial<OutcomeEvent>): OutcomeEvent {
  return {
    id: 'o1',
    posting_id: null,
    received_at: '2026-01-01T00:00:00Z',
    stage: 'rejection_post_screen',
    confidence: null,
    company_name: null,
    subject: 'Update on your application',
    from_domain: 'x.com',
    email_thread_id: 't1',
    target_company_id: null,
    ...partial,
  };
}

function rejectedPosting(id: string, name: string): PostingListItem {
  return {
    id,
    company: { id: `c-${id}`, name, domain: null, description: null, tier: 2 },
    role: {
      title: 'Senior PM',
      family: 'product_management',
      department: null,
      team: null,
      seniority: null,
    },
    state: {
      current: null,
      reason: null,
      snooze_until: null,
      current_at: null,
      resolved_status: 'rejected',
    },
    first_seen_at: '2026-01-01T00:00:00Z',
  } as unknown as PostingListItem;
}

describe('UnifiedRejectedList', () => {
  beforeEach(() => mockOutcomes.mockReset());

  test('shows only rejected entries — alive ones are filtered out', () => {
    setOutcomes([
      oe({
        id: 'r1',
        email_thread_id: 'tr',
        stage: 'rejection_post_screen',
        company_name: 'RejCo',
      }),
      oe({
        id: 'a1',
        email_thread_id: 'ta',
        stage: 'application_confirmation',
        company_name: 'AliveCo',
      }),
    ]);
    render(<UnifiedRejectedList manualPostings={[]} sort="applied" />);
    expect(screen.getByText('RejCo')).toBeInTheDocument();
    expect(screen.queryByText('AliveCo')).not.toBeInTheDocument();
  });

  test('a Gmail-only rejection is source-tagged gmail', () => {
    setOutcomes([
      oe({ id: 'r1', email_thread_id: 'tr', stage: 'rejection_pre_screen', company_name: 'RejCo' }),
    ]);
    render(<UnifiedRejectedList manualPostings={[]} sort="applied" />);
    expect(screen.getByTestId('source-chip-gmail')).toBeInTheDocument();
  });

  test('a manually-rejected posting appears, source-tagged manual', () => {
    setOutcomes([]);
    render(
      <UnifiedRejectedList
        manualPostings={[rejectedPosting('p1', 'ManualRejCo')]}
        sort="applied"
      />,
    );
    expect(screen.getByText('ManualRejCo')).toBeInTheDocument();
    expect(screen.getByTestId('source-chip-manual')).toBeInTheDocument();
  });

  test('a manual rejection matched to a Gmail rejection collapses to one "both" entry', () => {
    setOutcomes([
      oe({
        id: 'r1',
        posting_id: 'p1',
        email_thread_id: 'tr',
        stage: 'rejection_post_interview',
        company_name: 'BothCo',
      }),
    ]);
    render(
      <UnifiedRejectedList manualPostings={[rejectedPosting('p1', 'BothCo')]} sort="applied" />,
    );
    expect(screen.getAllByTestId('unified-applied-row')).toHaveLength(1);
    expect(screen.getByTestId('source-chip-both')).toBeInTheDocument();
  });

  test('empty state when nothing is rejected', () => {
    setOutcomes([oe({ stage: 'application_confirmation', subject: 'Applying to AliveCo' })]);
    render(<UnifiedRejectedList manualPostings={[]} sort="applied" />);
    expect(screen.getByTestId('rejected-empty')).toBeInTheDocument();
  });
});
