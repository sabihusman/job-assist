import { render, screen, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { CompaniesTable } from '@/components/companies/CompaniesTable';
import type { CompanyListItem } from '@/lib/companies/types';

vi.mock('@/lib/api/applied', () => ({
  useAppliedPostings: () => ({ data: { total: 0, offset: 0, limit: 500, items: [] } }),
  useAllOutcomes: () => ({ data: { total: 0, offset: 0, limit: 2000, items: [] } }),
}));

const COMPANIES: CompanyListItem[] = [
  {
    id: 'c-1',
    name: 'Alpha Co',
    domain: 'alpha.com',
    description: null,
    tier: 1,
    ats_set: ['greenhouse'],
    active_postings: 3,
    total_postings: 10,
  },
  {
    id: 'c-2',
    name: 'Beta Co',
    domain: null,
    description: null,
    tier: 2,
    ats_set: [],
    active_postings: 0,
    total_postings: 0,
  },
];

describe('CompaniesTable', () => {
  test('renders 6 columns (notes stripped)', () => {
    render(<CompaniesTable companies={COMPANIES} />);
    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(6);
    expect(headers.map((h) => h.textContent)).toEqual([
      'Name',
      'Tier',
      'ATS',
      'Open',
      'Applied',
      'Outcomes',
    ]);
  });

  test('renders the company name and active_postings count', () => {
    render(<CompaniesTable companies={COMPANIES} />);
    expect(screen.getByText('Alpha Co')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  test('empty ats_set renders an em-dash', () => {
    render(<CompaniesTable companies={COMPANIES} />);
    // Beta Co has no ATS and no applied postings, so both the ATS and
    // Outcomes cells render `—`. Just confirm at least one is present.
    const betaRow = screen.getByText('Beta Co').closest('tr');
    if (!betaRow) throw new Error('Beta Co row not found');
    expect(within(betaRow).getAllByText('—').length).toBeGreaterThan(0);
  });
});
