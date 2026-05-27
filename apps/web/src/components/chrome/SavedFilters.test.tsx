import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, test, vi } from 'vitest';

import { SavedFilters } from '@/components/chrome/SavedFilters';

/**
 * PR #72 — saved-filter highlight regression tests.
 *
 * The old check was raw ``URLSearchParams.toString()`` equality.
 * That worked for single-value filters ("T1 · Remote · Not reviewed")
 * but quietly failed on multi-value filters ("T1+T2 · PM" → URL
 * ``?tier=1&tier=2&...``) because multi-value keys are
 * insertion-order-sensitive. Normalized comparison fixes both.
 *
 * The tests drive ``useSearchParams`` / ``usePathname`` directly via
 * mocks and assert ``data-active`` on the matching Link. The badge
 * counts hit ``useSavedFilterCount`` which we stub to return zero.
 */

const { getMock, pathnameMock, searchParamsMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  pathnameMock: vi.fn(),
  searchParamsMock: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => pathnameMock(),
  useSearchParams: () => searchParamsMock(),
}));

vi.mock('@/lib/api/client', () => ({
  api: { GET: getMock, POST: vi.fn(), PATCH: vi.fn() },
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function setUrl(pathname: string, search: string) {
  pathnameMock.mockReturnValue(pathname);
  searchParamsMock.mockReturnValue(new URLSearchParams(search));
}

describe('SavedFilters highlight', () => {
  test('highlights T1 · Remote · Not reviewed on its single-value URL', () => {
    setUrl('/', 'tier=1&remote_type=remote&state=triage');
    getMock.mockResolvedValue({
      data: { total: 0, offset: 0, limit: 1, items: [] },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    render(wrap(<SavedFilters collapsed={false} />));
    const link = screen.getByRole('link', { name: 'T1 · Remote · Not reviewed' });
    expect(link.getAttribute('data-active')).toBe('true');
  });

  test('highlights T1+T2 · PM on its multi-value URL (PR #72)', () => {
    // Multi-value ``tier`` is the regression. Pre-fix this returned
    // ``data-active="false"`` because raw toString() insertion-order
    // didn't match the literal href string.
    setUrl('/', 'tier=1&tier=2&role_family=product_management&state=triage');
    getMock.mockResolvedValue({
      data: { total: 0, offset: 0, limit: 1, items: [] },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    render(wrap(<SavedFilters collapsed={false} />));
    const link = screen.getByRole('link', { name: 'T1+T2 · PM' });
    expect(link.getAttribute('data-active')).toBe('true');
  });

  test('highlights T1+T2 · PM even when multi-value tier is reordered', () => {
    // Same URL but with tier=2 BEFORE tier=1 — should still match
    // because the normalized comparator sorts pairs.
    setUrl('/', 'tier=2&tier=1&role_family=product_management&state=triage');
    getMock.mockResolvedValue({
      data: { total: 0, offset: 0, limit: 1, items: [] },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    render(wrap(<SavedFilters collapsed={false} />));
    const link = screen.getByRole('link', { name: 'T1+T2 · PM' });
    expect(link.getAttribute('data-active')).toBe('true');
  });

  test('does NOT highlight a non-matching URL', () => {
    setUrl('/', 'tier=3&state=triage');
    getMock.mockResolvedValue({
      data: { total: 0, offset: 0, limit: 1, items: [] },
      error: null,
      response: new Response(null, { status: 200 }),
    });

    render(wrap(<SavedFilters collapsed={false} />));
    for (const label of ['T1 · Remote · Not reviewed', 'T1+T2 · PM', 'Snoozed > 7d']) {
      const link = screen.getByRole('link', { name: label });
      expect(link.getAttribute('data-active')).toBe('false');
    }
  });
});
