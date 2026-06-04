import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import type { IngestStats } from '@/lib/api/ingest';

const holder = vi.hoisted(() => ({
  data: undefined as IngestStats | undefined,
  isLoading: false,
  isError: false,
}));
vi.mock('@/lib/api/ingest', () => ({ useIngestStats: () => holder }));

import { IngestPanel } from '@/components/stats/IngestPanel';

describe('IngestPanel', () => {
  beforeEach(() => {
    holder.data = undefined;
    holder.isLoading = false;
    holder.isError = false;
  });

  test('renders daily new-posting counts and per-source status (green/red)', () => {
    holder.data = {
      window_days: 14,
      totals: { runs: 5, successes: 4, failures: 1, postings_new: 42 },
      daily: [
        { day: '2026-06-03', postings_new: 12, postings_fetched: 30, runs: 1, failures: 0 },
        { day: '2026-06-02', postings_new: 0, postings_fetched: 10, runs: 1, failures: 1 },
      ],
      by_source: [
        {
          source: 'greenhouse',
          status: 'success',
          last_run_at: '2026-06-03T06:00:00Z',
          postings_new: 12,
        },
        { source: 'lever', status: 'failed', last_run_at: '2026-06-02T06:00:00Z', postings_new: 0 },
      ],
    };
    render(<IngestPanel />);

    // totals KPI (new postings over the window)
    expect(screen.getByText('42')).toBeInTheDocument();

    // per-source: both sources + the failed status surfaced
    const bySource = screen.getByTestId('ingest-by-source');
    expect(within(bySource).getByText('greenhouse')).toBeInTheDocument();
    expect(within(bySource).getByText('lever')).toBeInTheDocument();
    expect(within(bySource).getByText('failed')).toBeInTheDocument();

    // daily new-posting counts + a failed-day marker
    const daily = screen.getByTestId('ingest-daily');
    expect(within(daily).getByText('12')).toBeInTheDocument();
    expect(within(daily).getByText('1 failed')).toBeInTheDocument();
  });

  test('empty state when no runs recorded', () => {
    holder.data = {
      window_days: 14,
      totals: { runs: 0, successes: 0, failures: 0, postings_new: 0 },
      daily: [],
      by_source: [],
    };
    render(<IngestPanel />);
    expect(screen.getByText(/no ingest runs recorded/i)).toBeInTheDocument();
  });

  test('error state', () => {
    holder.isError = true;
    render(<IngestPanel />);
    expect(screen.getByText(/couldn't load ingest health/i)).toBeInTheDocument();
  });
});
