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
  test('bare URL applies BOTH resolved defaults: state=triage AND PM/PO families', () => {
    // Regression guard: a bare triage URL carries no `state` param, but the
    // list view defaults state to ['triage'] (parseFilters). The export must
    // reproduce that default — without it the xlsx dumped every state
    // (applied/rejected/snoozed), not just the pending rows the operator sees.
    setParams('');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.tagName).toBe('A');
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?state=triage&role_family=product_management&role_family=product_owner',
    );
  });

  test('forwards the current search params + applies the PM/PO-only default', () => {
    setParams('tier=1&tier=2&sort=best_fit&state=triage');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?tier=1&tier=2&state=triage&sort=best_fit&role_family=product_management&role_family=product_owner',
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
      'http://api.test/postings/export.xlsx?state=triage&role_family=product_marketing',
    );
  });

  test('an explicit non-triage state (e.g. a saved filter row) is preserved, not overridden', () => {
    setParams('state=snoozed&include_snoozed_past_only=true');
    render(<ExportButton />);
    const a = screen.getByTestId('triage-export-button') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe(
      'http://api.test/postings/export.xlsx?state=snoozed&include_snoozed_past_only=true&role_family=product_management&role_family=product_owner',
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
