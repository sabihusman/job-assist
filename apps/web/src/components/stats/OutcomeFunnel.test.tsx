import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { OutcomeFunnel } from '@/components/stats/OutcomeFunnel';

const ROWS = [
  { stage: 'Applied', count: 64 },
  { stage: 'Recruiter screen', count: 22 },
  { stage: 'Phone interview', count: 14 },
  { stage: 'Video interview', count: 9 },
  { stage: 'Onsite', count: 4 },
  { stage: 'Offer', count: 1 },
];

describe('OutcomeFunnel', () => {
  test('renders all 6 stages', () => {
    render(<OutcomeFunnel rows={ROWS} />);
    for (const row of ROWS) {
      expect(screen.getByText(row.stage)).toBeInTheDocument();
    }
  });

  test('renders drop-off indicators on non-final rows', () => {
    render(<OutcomeFunnel rows={ROWS} />);
    // Applied → Recruiter screen: 64 → 22 = ↓66%
    expect(screen.getByText(/↓66%/)).toBeInTheDocument();
  });

  test('drop-off omitted on final row', () => {
    render(<OutcomeFunnel rows={ROWS} />);
    const offerRow = screen.getByText('Offer').closest('li');
    // Final row's drop-off span is blank.
    expect(offerRow?.textContent ?? '').not.toMatch(/↓\d+%/);
  });

  test('renders nothing when given an empty array', () => {
    const { container } = render(<OutcomeFunnel rows={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
