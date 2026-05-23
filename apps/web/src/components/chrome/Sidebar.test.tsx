import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { Sidebar } from '@/components/chrome/Sidebar';
import { useUiStore } from '@/lib/stores/ui';
import { renderWithProviders } from '@/test-utils/render-with-providers';

// Stub next/navigation — Sidebar uses usePathname; SavedFilters reads
// useSearchParams. Returning an empty URLSearchParams is enough for the
// tests below.
vi.mock('next/navigation', () => ({
  usePathname: () => '/pipeline',
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  // Reset zustand store between tests so collapse state doesn't leak.
  useUiStore.setState({ sidebarCollapsed: false });
});

describe('Sidebar', () => {
  test('renders all primary nav items (8 entries after PR #50)', () => {
    renderWithProviders(<Sidebar />);
    const nav = screen.getByRole('navigation', { name: /^primary$/i });
    const links = within(nav).getAllByRole('link');
    const labels = links.map((l) => l.textContent ?? '');
    expect(labels.some((l) => l.includes('Triage'))).toBe(true);
    expect(labels.some((l) => l.includes('Applied'))).toBe(true);
    // PR #50: Passed and Rejected slot under Applied.
    expect(labels.some((l) => l.includes('Passed'))).toBe(true);
    expect(labels.some((l) => l.includes('Rejected'))).toBe(true);
    expect(labels.some((l) => l.includes('Pipeline'))).toBe(true);
    expect(labels.some((l) => l.includes('Companies'))).toBe(true);
    expect(labels.some((l) => l.includes('Stats'))).toBe(true);
    expect(labels.some((l) => l.includes('Settings'))).toBe(true);
    expect(links).toHaveLength(8);
  });

  test('Passed and Rejected nav links point at their pages', () => {
    renderWithProviders(<Sidebar />);
    const passed = screen.getByRole('link', { name: /passed/i });
    const rejected = screen.getByRole('link', { name: /rejected/i });
    expect(passed.getAttribute('href')).toBe('/passed');
    expect(rejected.getAttribute('href')).toBe('/rejected');
  });

  test('active route is marked aria-current=page', () => {
    renderWithProviders(<Sidebar />);
    const pipeline = screen.getByRole('link', { name: /pipeline/i });
    expect(pipeline.getAttribute('aria-current')).toBe('page');
    const triage = screen.getByRole('link', { name: /triage/i });
    expect(triage.getAttribute('aria-current')).toBeNull();
  });

  test('renders the three hardcoded saved filters', () => {
    renderWithProviders(<Sidebar />);
    const filtersNav = screen.getByRole('navigation', { name: /saved filters/i });
    expect(within(filtersNav).getAllByRole('link')).toHaveLength(3);
    expect(filtersNav.textContent).toContain('T1 · Remote · Not reviewed');
    // Row #2 was renamed from "Staff PM · $200k+" to "T1+T2 · PM" in PR
    // #32b — the API has no salary_min/seniority filter so the original
    // label couldn't be wired to real URL params.
    expect(filtersNav.textContent).toContain('T1+T2 · PM');
    expect(filtersNav.textContent).toContain('Snoozed > 7d');
  });

  test('collapse toggle hides labels and saved filters', async () => {
    renderWithProviders(<Sidebar />);
    await waitFor(() => expect(screen.getByText('Job Assist')).toBeInTheDocument());

    act(() => {
      useUiStore.getState().toggleSidebar();
    });

    await waitFor(() => expect(screen.queryByText('Job Assist')).not.toBeInTheDocument());
    expect(screen.queryByRole('navigation', { name: /saved filters/i })).not.toBeInTheDocument();
  });
});
