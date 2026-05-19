import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { AppliedList } from '@/components/applied/AppliedList';
import type { PostingListItem } from '@/lib/triage/types';

vi.mock('@/lib/api/applied', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/applied')>();
  return {
    ...actual,
    useAllOutcomes: () => ({ data: { total: 0, offset: 0, limit: 2000, items: [] } }),
    usePostingOutcomes: () => ({ data: undefined, isLoading: false }),
  };
});

function posting(id: string, tier: number, appliedDaysAgo: number): PostingListItem {
  const iso = new Date(Date.now() - appliedDaysAgo * 86_400_000).toISOString();
  return {
    id,
    company: { id: `c-${id}`, name: `Co${id}`, domain: null, description: null, tier },
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

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe('AppliedList', () => {
  test('renders one row per posting', () => {
    wrap(<AppliedList postings={[posting('a', 1, 1), posting('b', 2, 2)]} sort="applied" />);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  test('empty state when there are no postings', () => {
    wrap(<AppliedList postings={[]} sort="applied" />);
    expect(screen.getByTestId('applied-empty')).toBeInTheDocument();
  });

  test('sort=tier orders by ascending tier', () => {
    wrap(<AppliedList postings={[posting('p2', 2, 1), posting('p1', 1, 5)]} sort="tier" />);
    const items = screen.getAllByRole('listitem');
    expect(items[0]?.textContent).toContain('Cop1');
    expect(items[1]?.textContent).toContain('Cop2');
  });

  test('sort=applied orders by most recent applied_at first', () => {
    wrap(
      <AppliedList
        postings={[posting('old', 1, 10), posting('new', 1, 1), posting('mid', 1, 5)]}
        sort="applied"
      />,
    );
    const items = screen.getAllByRole('listitem');
    expect(items[0]?.textContent).toContain('Conew');
    expect(items[2]?.textContent).toContain('Coold');
  });
});
