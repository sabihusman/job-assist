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
    // feat/pm-po-only-filter: bare params → PM/PO-only default is applied so
    // the export == the gated list view.
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?role_family=product_management&role_family=product_owner',
    );
  });

  test('forwards the current search params + applies the PM/PO-only default', () => {
    setParams('tier=1&tier=2&sort=best_fit&state=triage');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?tier=1&tier=2&sort=best_fit&state=triage&role_family=product_management&role_family=product_owner',
    );
  });

  test('pm_only=false (gate off) exports all families — no role_family injected', () => {
    setParams('pm_only=false&state=triage');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    // pm_only is a frontend concept — it is dropped, and nothing is injected.
    expect(a.getAttribute('href')).toBe('http://api.test/postings/export.xlsx?state=triage');
  });

  test('explicit role_family overrides the default — left untouched', () => {
    setParams('role_family=product_marketing&state=triage');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?role_family=product_marketing&state=triage',
    );
  });

  test('has the download attribute as a hint to the browser', () => {
    setParams('');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button');
    expect(a.hasAttribute('download')).toBe(true);
  });

  test('displays the "Export current view" label (no row-cap claim)', () => {
    setParams('');
    render(<ExportButton />);
    expect(screen.getByText(/export current view/i)).toBeInTheDocument();
    // The old "top 40" cap is gone — the label must not imply one.
    expect(screen.queryByText(/top 40/i)).not.toBeInTheDocument();
  });
});
