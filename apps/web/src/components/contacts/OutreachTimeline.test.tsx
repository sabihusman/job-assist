import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, test, vi } from 'vitest';

import { OutreachTimeline } from '@/components/contacts/OutreachTimeline';
import type { OutreachMessage } from '@/lib/contacts/types';

vi.mock('@/lib/api/client', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() },
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeMsg(overrides: Partial<OutreachMessage> = {}): OutreachMessage {
  return {
    id: `m-${Math.random().toString(36).slice(2, 8)}`,
    contact_id: 'c-1',
    direction: 'outbound',
    channel: 'linkedin',
    subject: 'Test subject',
    body: 'Test body',
    sent_at: '2026-06-03T12:00:00Z',
    posting_id: null,
    source: 'manual',
    external_message_id: null,
    metadata: null,
    created_at: '2026-06-03T12:00:00Z',
    ...overrides,
  };
}

describe('OutreachTimeline', () => {
  test('renders one row per message in input order', () => {
    const items = [
      makeMsg({ id: 'm-1', subject: 'First' }),
      makeMsg({ id: 'm-2', subject: 'Second' }),
      makeMsg({ id: 'm-3', subject: 'Third' }),
    ];
    render(
      wrap(
        <OutreachTimeline contactId="c-1" items={items} total={3} isLoading={false} />,
      ),
    );

    const rows = screen.getAllByTestId('outreach-row');
    expect(rows).toHaveLength(3);
    // Input order is preserved — the parent ``useContactOutreach`` already
    // sorts newest-first server-side, so the timeline just renders.
    expect(rows[0]).toHaveTextContent('First');
    expect(rows[1]).toHaveTextContent('Second');
    expect(rows[2]).toHaveTextContent('Third');
  });

  test('empty state when items is empty and not loading', () => {
    render(wrap(<OutreachTimeline contactId="c-1" items={[]} total={0} isLoading={false} />));
    expect(screen.getByTestId('outreach-timeline-empty')).toBeInTheDocument();
    expect(screen.getByText(/no outreach logged yet/i)).toBeInTheDocument();
  });

  test('loading skeleton when isLoading', () => {
    render(wrap(<OutreachTimeline contactId="c-1" items={[]} total={0} isLoading={true} />));
    expect(screen.getByTestId('outreach-timeline-loading')).toBeInTheDocument();
  });

  test('inbound direction row has positive-tone styling via data-direction', () => {
    const items = [makeMsg({ direction: 'inbound', channel: 'email' })];
    render(wrap(<OutreachTimeline contactId="c-1" items={items} total={1} isLoading={false} />));
    const row = screen.getByTestId('outreach-row');
    expect(row).toHaveAttribute('data-direction', 'inbound');
    expect(row).toHaveAttribute('data-channel', 'email');
  });

  test('Load more button renders only when total > items.length', () => {
    const items = [makeMsg(), makeMsg()];
    const { rerender } = render(
      wrap(<OutreachTimeline contactId="c-1" items={items} total={5} isLoading={false} />),
    );
    expect(screen.getByRole('button', { name: /Load more/i })).toBeInTheDocument();

    rerender(
      wrap(<OutreachTimeline contactId="c-1" items={items} total={2} isLoading={false} />),
    );
    expect(screen.queryByRole('button', { name: /Load more/i })).not.toBeInTheDocument();
  });

  test('gmail_auto badge surfaces on auto-detected messages', () => {
    // PR #52 only writes ``manual``; this lock-in covers PR #53's
    // forward-compat rendering — a row with ``source='gmail_auto'``
    // must be visually distinguishable.
    const items = [makeMsg({ source: 'gmail_auto', external_message_id: 'gmail-xyz' })];
    render(wrap(<OutreachTimeline contactId="c-1" items={items} total={1} isLoading={false} />));
    expect(screen.getByTestId('gmail-auto-badge')).toBeInTheDocument();
  });
});
