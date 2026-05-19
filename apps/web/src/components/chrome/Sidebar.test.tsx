import { render, screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { Sidebar } from '@/components/chrome/Sidebar';
import { useUiStore } from '@/lib/stores/ui';

// Stub next/navigation — Sidebar uses usePathname.
vi.mock('next/navigation', () => ({
  usePathname: () => '/pipeline',
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  // Reset zustand store between tests so collapse state doesn't leak.
  useUiStore.setState({ sidebarCollapsed: false });
});

describe('Sidebar', () => {
  test('renders all six primary nav items and no Outreach', () => {
    render(<Sidebar />);
    const nav = screen.getByRole('navigation', { name: /^primary$/i });
    const links = within(nav).getAllByRole('link');
    const labels = links.map((l) => l.textContent ?? '');
    expect(labels.some((l) => l.includes('Triage'))).toBe(true);
    expect(labels.some((l) => l.includes('Applied'))).toBe(true);
    expect(labels.some((l) => l.includes('Pipeline'))).toBe(true);
    expect(labels.some((l) => l.includes('Companies'))).toBe(true);
    expect(labels.some((l) => l.includes('Stats'))).toBe(true);
    expect(labels.some((l) => l.includes('Settings'))).toBe(true);
    expect(labels.some((l) => /outreach/i.test(l))).toBe(false);
    expect(links).toHaveLength(6);
  });

  test('active route is marked aria-current=page', () => {
    render(<Sidebar />);
    const pipeline = screen.getByRole('link', { name: /pipeline/i });
    expect(pipeline.getAttribute('aria-current')).toBe('page');
    const triage = screen.getByRole('link', { name: /triage/i });
    expect(triage.getAttribute('aria-current')).toBeNull();
  });

  test('renders the three hardcoded saved filters', () => {
    render(<Sidebar />);
    const filtersNav = screen.getByRole('navigation', { name: /saved filters/i });
    expect(within(filtersNav).getAllByRole('link')).toHaveLength(3);
    expect(filtersNav.textContent).toContain('T1 · Remote · Not reviewed');
    expect(filtersNav.textContent).toContain('Staff PM · $200k+');
    expect(filtersNav.textContent).toContain('Snoozed > 7d');
  });

  test('collapse toggle hides labels and saved filters', async () => {
    render(<Sidebar />);
    // First-paint default = expanded, including after the `mounted` effect.
    await waitFor(() => expect(screen.getByText('Job Assist')).toBeInTheDocument());

    // Mutate the store directly (the trigger button lives in the banner,
    // not the sidebar). `act` flushes the React update so the next assertion
    // sees the collapsed DOM.
    act(() => {
      useUiStore.getState().toggleSidebar();
    });

    await waitFor(() => expect(screen.queryByText('Job Assist')).not.toBeInTheDocument());
    expect(screen.queryByRole('navigation', { name: /saved filters/i })).not.toBeInTheDocument();
  });
});
