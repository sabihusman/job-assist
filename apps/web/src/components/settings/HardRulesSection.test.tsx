import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { HardRulesSection } from '@/components/settings/HardRulesSection';
import type { OperatorProfileRead } from '@/lib/settings/types';

/**
 * PR #43 vitest coverage for HardRulesSection.
 *
 * Verifies the new salary-ceiling and seniority-levels controls render
 * correctly, the SeniorityChips toggle behavior is right, and the
 * empty-state footnote appears. The full save round-trip lives in
 * the Playwright e2e suite (settings.spec.ts).
 */

const mockMutate = vi.fn();
vi.mock('@/lib/api/settings', () => ({
  useUpdateProfile: () => ({
    mutateAsync: mockMutate,
    isPending: false,
    error: null,
  }),
}));

function profile(overrides: Partial<OperatorProfileRead> = {}): OperatorProfileRead {
  return {
    id: 1,
    looking_for_text: '',
    role_keywords: [],
    geo_whitelist: [],
    salary_floor_usd: 85_000,
    salary_ceiling_usd: null,
    applicant_cap: 500,
    per_company_cap: 3,
    similarity_weight: 0,
    staffing_firm_blocklist: [],
    seniority_levels_included: null,
    created_at: '2026-05-26T00:00:00Z',
    updated_at: '2026-05-26T00:00:00Z',
    ...overrides,
  };
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe('HardRulesSection — PR #43 controls', () => {
  test('renders the salary ceiling input alongside the floor', () => {
    wrap(<HardRulesSection profile={profile()} />);
    expect(screen.getByLabelText('Salary ceiling')).toBeInTheDocument();
    expect(screen.getByLabelText('Salary floor')).toBeInTheDocument();
  });

  test('ceiling displays "No ceiling" when value is 0', () => {
    wrap(<HardRulesSection profile={profile()} />);
    // Two readouts are present (floor + ceiling). Ceiling reads "No ceiling"
    // because the loaded profile has null → form maps to 0 → display
    // helper returns the muted label.
    expect(screen.getByText('No ceiling')).toBeInTheDocument();
  });

  test('seniority chips render all 6 PM levels', () => {
    wrap(<HardRulesSection profile={profile()} />);
    // Six toggle buttons (Intern / APM / PM / Senior PM / Lead PM / Principal PM)
    // — query by aria-pressed which the SeniorityChips component sets on each.
    const chips = screen.getAllByRole('button').filter((b) => b.hasAttribute('aria-pressed'));
    expect(chips).toHaveLength(6);
  });

  test('empty selection shows the muted "all levels included" footnote', () => {
    wrap(<HardRulesSection profile={profile({ seniority_levels_included: [] })} />);
    expect(screen.getByText(/all seniority levels are currently included/i)).toBeInTheDocument();
  });

  test('clicking a chip toggles aria-pressed', async () => {
    const user = userEvent.setup();
    wrap(<HardRulesSection profile={profile()} />);
    const apmChip = screen.getByRole('button', { name: 'APM' });
    expect(apmChip).toHaveAttribute('aria-pressed', 'false');
    await user.click(apmChip);
    expect(apmChip).toHaveAttribute('aria-pressed', 'true');
    expect(
      screen.queryByText(/all seniority levels are currently included/i),
    ).not.toBeInTheDocument();
  });

  test('selecting then unselecting a chip restores empty state', async () => {
    const user = userEvent.setup();
    wrap(<HardRulesSection profile={profile()} />);
    const apmChip = screen.getByRole('button', { name: 'APM' });
    await user.click(apmChip);
    await user.click(apmChip);
    expect(apmChip).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/all seniority levels are currently included/i)).toBeInTheDocument();
  });

  // feat/tunable-per-company-cap — the reachability fix. These prove the
  // operator can actually SEE and MOVE the cap (the gap), and that moving it
  // is sent to the backend, per the verification standard.
  test('renders the "Roles per company" control showing the current value', () => {
    wrap(<HardRulesSection profile={profile({ per_company_cap: 3 })} />);
    const input = screen.getByLabelText('Roles per company');
    expect(input).toBeInTheDocument();
    expect(input).toHaveValue(3);
  });

  test('per_company_cap=0 displays "Unlimited"', () => {
    wrap(<HardRulesSection profile={profile({ per_company_cap: 0 })} />);
    expect(screen.getByText('Unlimited')).toBeInTheDocument();
  });

  test('changing the cap and saving sends per_company_cap to the backend', async () => {
    const user = userEvent.setup();
    mockMutate.mockClear();
    mockMutate.mockResolvedValueOnce(undefined);
    wrap(<HardRulesSection profile={profile({ per_company_cap: 3 })} />);

    const input = screen.getByLabelText('Roles per company');
    await user.clear(input);
    await user.type(input, '8');

    // Submit → confirm modal → Save changes → mutateAsync.
    await user.click(screen.getByRole('button', { name: /save hard rules/i }));
    await user.click(await screen.findByRole('button', { name: /save changes/i }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate.mock.calls[0][0]).toMatchObject({ per_company_cap: 8 });
  });

  // Slice 2b — the "Semantic weight" control sends similarity_weight (off by
  // default; the operator must be able to see it and move it, per rule 2).
  test('renders the "Semantic weight" control, off by default', () => {
    wrap(<HardRulesSection profile={profile({ similarity_weight: 0 })} />);
    expect(screen.getByLabelText('Semantic weight')).toBeInTheDocument();
    expect(screen.getByText('Off')).toBeInTheDocument();
  });

  test('changing the semantic weight and saving sends similarity_weight', async () => {
    const user = userEvent.setup();
    mockMutate.mockClear();
    mockMutate.mockResolvedValueOnce(undefined);
    wrap(<HardRulesSection profile={profile({ similarity_weight: 0 })} />);

    const input = screen.getByLabelText('Semantic weight');
    await user.clear(input);
    await user.type(input, '0.5');

    await user.click(screen.getByRole('button', { name: /save hard rules/i }));
    await user.click(await screen.findByRole('button', { name: /save changes/i }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate.mock.calls[0][0]).toMatchObject({ similarity_weight: 0.5 });
  });

  test('pre-selected seniority levels render as pressed', () => {
    wrap(<HardRulesSection profile={profile({ seniority_levels_included: ['apm', 'pm'] })} />);
    expect(screen.getByRole('button', { name: 'APM' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'PM' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Senior PM' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});
