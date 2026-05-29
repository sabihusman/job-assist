import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { ExportButton } from '@/components/triage/ExportButton';

let currentParams = '';

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(currentParams),
}));

vi.mock('@/lib/api/client', () => ({
  API_BASE_URL: 'http://api.test',
}));

function setParams(s: string) {
  currentParams = s;
}

describe('ExportButton', () => {
  test('renders an anchor pointing at /postings/export.xlsx', () => {
    setParams('');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.tagName).toBe('A');
    expect(a.getAttribute('href')).toBe('http://api.test/postings/export.xlsx');
  });

  test('forwards the current search params verbatim', () => {
    setParams('tier=1&tier=2&sort=best_fit&state=triage');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?tier=1&tier=2&sort=best_fit&state=triage',
    );
  });

  test('has the download attribute as a hint to the browser', () => {
    setParams('');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button');
    expect(a.hasAttribute('download')).toBe(true);
  });

  test('displays the locked label "Export view (top 40)"', () => {
    setParams('');
    render(<ExportButton />);
    expect(screen.getByText(/export view \(top 40\)/i)).toBeInTheDocument();
  });
});
