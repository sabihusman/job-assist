import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { CalibrationCard } from '@/components/triage/CalibrationCard';

const mockState = vi.hoisted(() => ({ data: null as unknown }));

vi.mock('@/lib/api/hooks', () => ({
  useCalibration: () => ({
    data: mockState.data,
    isLoading: mockState.data === null,
  }),
}));

function wrap(node: React.ReactNode) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

afterEach(() => {
  mockState.data = null;
});

describe('CalibrationCard', () => {
  test('renders all four KPI values from the calibration response', () => {
    mockState.data = {
      window: { since: '2026-05-12T00:00:00Z', until: '2026-05-19T00:00:00Z' },
      surfaced: 42,
      interested: 13,
      interested_rate: 0.31,
      applied: 8,
      rejected_by_you: 11,
      top_rejected_role_families: [
        { role_family: 'program_management', count: 4 },
        { role_family: 'product_marketing', count: 2 },
      ],
    };
    wrap(<CalibrationCard />);
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('13')).toBeInTheDocument();
    expect(screen.getByText('(31%)')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.getByText('11')).toBeInTheDocument();
  });

  test('renders — when surfaced is 0 and rate is null (no division by zero)', () => {
    mockState.data = {
      window: { since: '', until: '' },
      surfaced: 0,
      interested: 0,
      interested_rate: null,
      applied: 0,
      rejected_by_you: 0,
      top_rejected_role_families: [],
    };
    wrap(<CalibrationCard />);
    expect(screen.getByText('(—)')).toBeInTheDocument();
  });

  test('renders — for top wrong reasons when array is empty', () => {
    mockState.data = {
      window: { since: '', until: '' },
      surfaced: 5,
      interested: 1,
      interested_rate: 0.2,
      applied: 0,
      rejected_by_you: 0,
      top_rejected_role_families: [],
    };
    wrap(<CalibrationCard />);
    // The label sits in its own <span> inside the reasons <p>. Walk up
    // to the paragraph and inspect aggregated text content — the empty
    // case renders just `Top "wrong" reasons: —`.
    const labelSpan = screen.getByText(/Top "wrong" reasons:/);
    const paragraph = labelSpan.closest('p');
    expect(paragraph?.textContent).toMatch(/Top "wrong" reasons:\s*—/);
  });
});
