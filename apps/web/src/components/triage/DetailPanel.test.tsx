import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { DetailPanel } from '@/components/triage/DetailPanel';
import type { PostingDetail } from '@/lib/triage/types';

const mockState = vi.hoisted(() => ({
  data: null as PostingDetail | null,
  isLoading: false,
}));

vi.mock('@/lib/api/hooks', () => ({
  usePosting: () => ({ data: mockState.data, isLoading: mockState.isLoading }),
}));

function makeDetail(overrides: Partial<PostingDetail> = {}): PostingDetail {
  return {
    id: 'p-detail-1',
    company: {
      id: 'c-1',
      name: 'DetailCo',
      domain: null,
      description: 'DetailCo description.',
      tier: 1,
    },
    role: {
      title: 'Detail Role',
      family: 'product_management',
      department: 'Product',
      team: null,
      seniority: 'senior_pm',
    },
    location_raw: 'San Francisco, CA',
    locations_normalized: ['San Francisco, CA'],
    remote_type: 'hybrid',
    salary: { min: 200000, max: 250000, currency: 'USD', period: 'annual' },
    source: { ats: 'ashby', url: 'https://example.test/jd' },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
    description_markdown: '## About the role\n\n- bullet one\n- bullet two',
    division: null,
    posted_at: null,
    last_seen_at: null,
    closed_at: null,
    state_history: [],
    ...overrides,
  };
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

afterEach(() => {
  mockState.data = null;
  mockState.isLoading = false;
});

describe('DetailPanel', () => {
  test('renders the empty state when no posting is selected', () => {
    wrap(<DetailPanel selectedId={null} onClose={() => {}} onAction={() => {}} />);
    expect(screen.getByText(/select a posting to see details/i)).toBeInTheDocument();
  });

  test('renders the division-pending callout when division is null', () => {
    mockState.data = makeDetail({ division: null });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(screen.getByText(/division info pending/i)).toBeInTheDocument();
  });

  test('renders the markdown JD', () => {
    mockState.data = makeDetail();
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(screen.getByRole('heading', { level: 2, name: /about the role/i }));
    expect(screen.getByText(/bullet one/)).toBeInTheDocument();
  });

  test('Open JD anchor targets a new tab', () => {
    mockState.data = makeDetail();
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    const anchor = screen.getByRole('link', { name: /open job description in new tab/i });
    expect(anchor.getAttribute('target')).toBe('_blank');
    expect(anchor.getAttribute('href')).toBe('https://example.test/jd');
  });
});
