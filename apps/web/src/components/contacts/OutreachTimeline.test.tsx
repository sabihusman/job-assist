import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { OutreachTimeline } from '@/components/contacts/OutreachTimeline';
import type { OutreachMessage } from '@/lib/contacts/types';

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock('@/lib/api/client', () => ({
  api: { GET: getMock, POST: vi.fn(), PATCH: vi.fn() },
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeMsg(id: string, overrides: Partial<OutreachMessage> = {}): OutreachMessage {
  return {
    id,
    contact_id: 'c-1',
    direction: 'outbound',
    channel: 'linkedin',
    subject: `subject ${id}`,
    body: `body ${id}`,
    sent_at: '2026-06-03T12:00:00Z',
    posting_id: null,
    source: 'manual',
    external_message_id: null,
    metadata: null,
    created_at: '2026-06-03T12:00:00Z',
    ...overrides,
  };
}

function ok(data: unknown) {
  return { data, error: null, response: new Response(null, { status: 200 }) };
}

beforeEach(() => {
  getMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('OutreachTimeline', () => {
  test('renders one row per message in server order', async () => {
    getMock.mockResolvedValue(
      ok({
        total: 3,
        offset: 0,
        limit: 50,
        items: [
          makeMsg('m-1', { subject: 'First' }),
          makeMsg('m-2', { subject: 'Second' }),
          makeMsg('m-3', { subject: 'Third' }),
        ],
      }),
    );
    render(wrap(<OutreachTimeline contactId="c-1" />));

    await waitFor(() => expect(screen.getAllByTestId('outreach-row')).toHaveLength(3));
    const rows = screen.getAllByTestId('outreach-row');
    expect(rows[0]).toHaveTextContent('First');
    expect(rows[1]).toHaveTextContent('Second');
    expect(rows[2]).toHaveTextContent('Third');
  });

  test('empty state when the contact has no outreach', async () => {
    getMock.mockResolvedValue(ok({ total: 0, offset: 0, limit: 50, items: [] }));
    render(wrap(<OutreachTimeline contactId="c-1" />));
    await waitFor(() => expect(screen.getByTestId('outreach-timeline-empty')).toBeInTheDocument());
    expect(screen.getByText(/no outreach logged yet/i)).toBeInTheDocument();
  });

  test('inbound direction row carries data-direction/data-channel', async () => {
    getMock.mockResolvedValue(
      ok({
        total: 1,
        offset: 0,
        limit: 50,
        items: [makeMsg('m-1', { direction: 'inbound', channel: 'email' })],
      }),
    );
    render(wrap(<OutreachTimeline contactId="c-1" />));
    const row = await screen.findByTestId('outreach-row');
    expect(row).toHaveAttribute('data-direction', 'inbound');
    expect(row).toHaveAttribute('data-channel', 'email');
  });

  test('gmail_auto badge surfaces on auto-detected messages', async () => {
    getMock.mockResolvedValue(
      ok({
        total: 1,
        offset: 0,
        limit: 50,
        items: [makeMsg('m-1', { source: 'gmail_auto', external_message_id: 'gmail-xyz' })],
      }),
    );
    render(wrap(<OutreachTimeline contactId="c-1" />));
    expect(await screen.findByTestId('gmail-auto-badge')).toBeInTheDocument();
  });

  // ── fix/audit #5: Load More ACCUMULATES, never drops the middle page ───────
  //
  // The old single-extra-slot implementation re-keyed one extra query on a
  // moving offset, so the second Load More replaced the first extra window
  // (page 2 of 3 vanished — you'd jump from rows 0–1 + 2–3 straight to
  // 0–1 + 4–5). useInfiniteQuery accumulates every loaded page instead.
  test('two Load More clicks accumulate all pages, none dropped', async () => {
    // 6 messages across three 2-row pages (page size is 50 in prod; the
    // mock just honours whatever offset arrives so the pages stay distinct).
    const pages: Record<number, OutreachMessage[]> = {
      0: [makeMsg('m-1'), makeMsg('m-2')],
      2: [makeMsg('m-3'), makeMsg('m-4')],
      4: [makeMsg('m-5'), makeMsg('m-6')],
    };
    getMock.mockImplementation(
      async (_path: string, opts: { params: { query: { offset: number } } }) => {
        const offset = opts.params.query.offset ?? 0;
        return ok({ total: 6, offset, limit: 50, items: pages[offset] ?? [] });
      },
    );

    render(wrap(<OutreachTimeline contactId="c-1" />));
    await waitFor(() => expect(screen.getAllByTestId('outreach-row')).toHaveLength(2));

    // First Load More → page at offset 2 appended.
    fireEvent.click(screen.getByRole('button', { name: /load more/i }));
    await waitFor(() => expect(screen.getAllByTestId('outreach-row')).toHaveLength(4));

    // Second Load More → page at offset 4 appended; the offset-2 page MUST
    // still be present (the regression dropped it here).
    fireEvent.click(screen.getByRole('button', { name: /load more/i }));
    await waitFor(() => expect(screen.getAllByTestId('outreach-row')).toHaveLength(6));

    const rows = screen.getAllByTestId('outreach-row');
    const text = rows.map((r) => r.textContent ?? '').join('|');
    // All six distinct messages, including the middle page (m-3/m-4).
    for (const id of [
      'subject m-1',
      'subject m-2',
      'subject m-3',
      'subject m-4',
      'subject m-5',
      'subject m-6',
    ]) {
      expect(text).toContain(id);
    }
    // Load More disappears once the full count is reached.
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument();
  });
});
