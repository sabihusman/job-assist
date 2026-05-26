import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { LogOutreachForm } from '@/components/contacts/LogOutreachForm';

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock('@/lib/api/client', () => ({
  api: { POST: postMock, GET: vi.fn(), PATCH: vi.fn() },
}));

// Sonner toasts: stub out so they don't blow up under jsdom.
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  postMock.mockReset();
});

describe('LogOutreachForm', () => {
  test('starts collapsed and expands on click', async () => {
    const user = userEvent.setup();
    render(wrap(<LogOutreachForm contactId="c-1" />));

    expect(screen.queryByTestId('log-outreach-form')).not.toBeInTheDocument();
    expect(screen.getByTestId('log-outreach-open')).toBeInTheDocument();

    await user.click(screen.getByTestId('log-outreach-open'));
    expect(screen.getByTestId('log-outreach-form')).toBeInTheDocument();
  });

  test('Cancel collapses without dispatching a mutation', async () => {
    const user = userEvent.setup();
    render(wrap(<LogOutreachForm contactId="c-1" />));
    await user.click(screen.getByTestId('log-outreach-open'));

    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('log-outreach-form')).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  test('Submit POSTs with the canonical wire-body shape', async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValue({
      data: { id: 'm-1', source: 'manual' },
      error: null,
      response: new Response(null, { status: 201 }),
    });

    render(wrap(<LogOutreachForm contactId="c-1" />));
    await user.click(screen.getByTestId('log-outreach-open'));

    // The form's defaults are outbound + linkedin + now. Just fire submit.
    await user.click(screen.getByRole('button', { name: 'Log' }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());

    const [path, opts] = postMock.mock.calls[0] as [
      string,
      { params: { path: { contact_id: string } }; body: Record<string, unknown> },
    ];
    expect(path).toBe('/contacts/{contact_id}/outreach');
    expect(opts.params.path.contact_id).toBe('c-1');
    // Wire-shape contract: direction, channel, sent_at all present;
    // source absent (server forces manual).
    expect(opts.body).toHaveProperty('direction', 'outbound');
    expect(opts.body).toHaveProperty('channel', 'linkedin');
    expect(opts.body).toHaveProperty('sent_at');
    expect(opts.body).not.toHaveProperty('source');
    expect(opts.body).not.toHaveProperty('contact_id');
  });

  test('switching direction radio changes the body value sent', async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValue({
      data: { id: 'm-1' },
      error: null,
      response: new Response(null, { status: 201 }),
    });

    render(wrap(<LogOutreachForm contactId="c-1" />));
    await user.click(screen.getByTestId('log-outreach-open'));

    // Switch to inbound — the radio is an <input type="radio"> wrapped
    // in a <label> with "Inbound" text.
    await user.click(screen.getByText('Inbound'));
    await user.click(screen.getByRole('button', { name: 'Log' }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());

    const [, opts] = postMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
    expect(opts.body).toHaveProperty('direction', 'inbound');
  });

  test('subject + body trim whitespace before submit; empty stays absent', async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValue({
      data: { id: 'm-1' },
      error: null,
      response: new Response(null, { status: 201 }),
    });

    render(wrap(<LogOutreachForm contactId="c-1" />));
    await user.click(screen.getByTestId('log-outreach-open'));

    // Trailing whitespace only — should be stripped, field omitted.
    const subjectInput = screen.getByPlaceholderText(/quick question/i);
    await user.type(subjectInput, '   ');
    await user.click(screen.getByRole('button', { name: 'Log' }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());

    const [, opts] = postMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
    expect(opts.body).not.toHaveProperty('subject');
    expect(opts.body).not.toHaveProperty('body');
  });
});
