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
    ats: 'greenhouse',
    ats_handle: 'alpha',
    notes: null,
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
    ats: null,
    ats_handle: null,
    notes: null,
  },
  // Paused company: previously-known adapter, handle cleared, notes
  // explain why (PR #65 Atlassian shape).
  {
    id: 'c-3',
    name: 'Paused Co',
    domain: null,
    description: null,
    tier: 3,
    ats_set: [],
    active_postings: 0,
    total_postings: 0,
    ats: 'lever',
    ats_handle: null,
    notes: 'Paused: ATS handle unknown, soft-paused until investigation completes',
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

  // ── PR #71 ─────────────────────────────────────────────────────────────

  test('company name links to Triage filtered by target_company_id', () => {
    render(<CompaniesTable companies={COMPANIES} />);
    const link = screen.getByRole('link', { name: 'Alpha Co' });
    expect(link).toHaveAttribute('href', '/?target_company_id=c-1&state=triage');
  });

  test('Paused badge appears only when ats_handle is null AND ats is a known adapter', () => {
    render(<CompaniesTable companies={COMPANIES} />);
    // Paused Co qualifies: ats='lever', ats_handle=null
    const pausedRow = screen.getByText('Paused Co').closest('tr');
    if (!pausedRow) throw new Error('Paused Co row not found');
    expect(within(pausedRow).getByText('Paused')).toBeInTheDocument();

    // Beta Co has no ats at all → not "paused", just unconfigured.
    const betaRow = screen.getByText('Beta Co').closest('tr');
    if (!betaRow) throw new Error('Beta Co row not found');
    expect(within(betaRow).queryByText('Paused')).toBeNull();

    // Alpha Co has a live handle → not paused.
    const alphaRow = screen.getByText('Alpha Co').closest('tr');
    if (!alphaRow) throw new Error('Alpha Co row not found');
    expect(within(alphaRow).queryByText('Paused')).toBeNull();
  });

  test('paused notes surface via the link title attribute (tooltip)', () => {
    render(<CompaniesTable companies={COMPANIES} />);
    const pausedLink = screen.getByRole('link', { name: 'Paused Co' });
    expect(pausedLink).toHaveAttribute(
      'title',
      'Paused: ATS handle unknown, soft-paused until investigation completes',
    );
  });
});
