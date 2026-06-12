import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import ContactsPage from '@/app/contacts/page';
import type { ContactListItem } from '@/lib/contacts/types';

/**
 * PR #72 — Contacts hybrid filter persistence tests.
 *
 * Source filter (enum) lives in the URL → toggleable via router.replace,
 * survives refresh, matches Triage URL contract.
 *
 * Search field stays in component state → PII discipline; typed names
 * shouldn't end up in browser history bars.
 */

const { getMock, replaceMock, pathnameMock, searchParamsMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  replaceMock: vi.fn(),
  pathnameMock: vi.fn(),
  searchParamsMock: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => pathnameMock(),
  useSearchParams: () => searchParamsMock(),
}));

vi.mock('@/lib/api/client', () => ({
  api: { GET: getMock, POST: vi.fn(), PATCH: vi.fn() },
  // feat/view-exports: the page renders an export link built on the base URL.
  API_BASE_URL: 'http://api.test',
}));

vi.mock('@/components/chrome/AppShell', () => ({
  AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

// ContactDetailPanel does its own /contacts/{id} fetch; stub it out so
// this test focuses on the filter wiring.
vi.mock('@/components/contacts/ContactDetailPanel', () => ({
  ContactDetailPanel: () => null,
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function setUrl(search: string) {
  pathnameMock.mockReturnValue('/contacts');
  searchParamsMock.mockReturnValue(new URLSearchParams(search));
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({
    data: { total: 0, offset: 0, limit: 50, items: [] },
    error: null,
    response: new Response(null, { status: 200 }),
  });
  replaceMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('ContactsPage URL ↔ state split (PR #72)', () => {
  test('source_type hydrates from URL on mount', async () => {
    setUrl('source_type=tippie_alumni');
    render(wrap(<ContactsPage />));

    // The Tippie alumni chip is pressed; others are not.
    const tippieChip = await screen.findByRole('button', { name: /tippie alumni/i });
    expect(tippieChip.getAttribute('aria-pressed')).toBe('true');

    const linkedinChip = screen.getByRole('button', { name: /linkedin/i });
    expect(linkedinChip.getAttribute('aria-pressed')).toBe('false');
  });

  test('toggling a source chip pushes source_type to the URL via router.replace', async () => {
    setUrl('');
    const user = userEvent.setup();
    render(wrap(<ContactsPage />));

    await user.click(await screen.findByRole('button', { name: /tippie alumni/i }));

    expect(replaceMock).toHaveBeenCalledTimes(1);
    const target = replaceMock.mock.calls[0][0] as string;
    expect(target).toMatch(/^\/contacts\?/);
    const qs = new URLSearchParams(target.split('?')[1]);
    expect(qs.getAll('source_type')).toEqual(['tippie_alumni']);
  });

  test('toggling an already-selected chip removes it from the URL', async () => {
    setUrl('source_type=tippie_alumni&source_type=warm_intro');
    const user = userEvent.setup();
    render(wrap(<ContactsPage />));

    await user.click(await screen.findByRole('button', { name: /tippie alumni/i }));

    const target = replaceMock.mock.calls[0][0] as string;
    const qs = new URLSearchParams(target.split('?')[1]);
    expect(qs.getAll('source_type')).toEqual(['warm_intro']);
  });

  test('typing in search does NOT change the URL (PII discipline)', async () => {
    setUrl('');
    const user = userEvent.setup();
    render(wrap(<ContactsPage />));

    const searchBox = await screen.findByPlaceholderText('name…');
    await user.type(searchBox, 'alice');

    // router.replace must not have fired for the search keystrokes.
    expect(replaceMock).not.toHaveBeenCalled();

    // Trust but verify: re-read the URL mock; it was set to '' and the
    // page never asked us to update it.
    await waitFor(() => {
      expect(searchBox).toHaveValue('alice');
    });
  });
});

// ── fix/contacts-pagination ─────────────────────────────────────────────

function buildContact(i: number): ContactListItem {
  return {
    id: `c${i}`,
    first_name: `First${i}`,
    last_name: `Last${i}`,
    preferred_first_name: null,
    email_primary: `c${i}@example.com`,
    email_secondary: null,
    linkedin_url: null,
    current_employer: 'Acme',
    current_position: 'PM',
    location_city: null,
    location_state: null,
    location_country: null,
    location_metro: null,
    source_type: 'tippie_alumni',
    target_company_id: null,
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
  };
}

describe('ContactsPage pagination (fix/contacts-pagination)', () => {
  test('Load More pages through the full set instead of capping at the first page', async () => {
    setUrl('');
    const all = [0, 1, 2, 3].map(buildContact);
    // Server returns 2 rows per call (sliced by offset); total stays 4 so
    // the page knows there are more after the first page.
    getMock.mockImplementation(
      (_path: string, opts: { params?: { query?: { offset?: number } } }) => {
        const offset = Number(opts?.params?.query?.offset ?? 0);
        return Promise.resolve({
          data: { total: 4, offset, limit: 100, items: all.slice(offset, offset + 2) },
          error: null,
          response: new Response(null, { status: 200 }),
        });
      },
    );

    const user = userEvent.setup();
    render(wrap(<ContactsPage />));

    // First page: 2 of 4 loaded, Load More offered.
    expect(await screen.findByText('2 of 4')).toBeInTheDocument();
    const loadMore = await screen.findByRole('button', { name: /load more/i });

    await user.click(loadMore);

    // Second page accumulates: 4 of 4, Load More gone.
    expect(await screen.findByText('4 of 4')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument();

    // The second fetch advanced the offset to the count already loaded (2).
    const offsets = getMock.mock.calls.map((c) => c[1]?.params?.query?.offset);
    expect(offsets).toContain(0);
    expect(offsets).toContain(2);
  });
});
