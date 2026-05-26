import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { ContactDetailPanel } from '@/components/contacts/ContactDetailPanel';
import type { ContactDetail, OutreachMessageListResponse } from '@/lib/contacts/types';

const { getMock, postMock, patchMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
}));

vi.mock('@/lib/api/client', () => ({
  api: { GET: getMock, POST: postMock, PATCH: patchMock },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeDetail(overrides: Partial<ContactDetail> = {}): ContactDetail {
  return {
    id: 'c-1',
    first_name: 'Test',
    last_name: 'Person',
    preferred_first_name: null,
    email_primary: 'test@example.test',
    email_secondary: null,
    linkedin_url: null,
    phone: null,
    current_employer: 'ExampleCorp',
    current_position: 'Senior PM',
    location_city: null,
    location_state: null,
    location_country: null,
    location_metro: null,
    source_type: 'linkedin_outreach',
    source_metadata: null,
    job_functions_of_interest: null,
    industries_of_interest: null,
    contact_opt_in: false,
    contact_opt_in_topics: null,
    notes: 'Original notes',
    target_company_id: null,
    archived_at: null,
    created_at: '2026-05-30T00:00:00Z',
    updated_at: '2026-05-30T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  patchMock.mockReset();
  // Default: GET /contacts/{id} returns a fake detail; GET .../outreach
  // returns an empty list. Individual tests can override.
  getMock.mockImplementation(async (path: string) => {
    if (path === '/contacts/{contact_id}') {
      return {
        data: makeDetail(),
        error: null,
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === '/contacts/{contact_id}/outreach') {
      return {
        data: {
          total: 0,
          offset: 0,
          limit: 50,
          items: [],
        } satisfies OutreachMessageListResponse,
        error: null,
        response: new Response(null, { status: 200 }),
      };
    }
    return { data: null, error: { detail: 'unmocked' }, response: new Response(null, { status: 500 }) };
  });
});

describe('ContactDetailPanel', () => {
  test('hidden when contactId is null', () => {
    render(wrap(<ContactDetailPanel contactId={null} onClose={() => {}} />));
    const panel = screen.getByTestId('contact-detail-panel');
    expect(panel).toHaveAttribute('data-open', 'false');
    expect(panel).toHaveAttribute('aria-hidden', 'true');
  });

  test('opens and renders contact name + source chip', async () => {
    render(wrap(<ContactDetailPanel contactId="c-1" onClose={() => {}} />));
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Test Person/i })).toBeInTheDocument(),
    );
    expect(screen.getByText(/LinkedIn outreach/i)).toBeInTheDocument();
  });

  test('Archive button calls POST /contacts/{id}/archive', async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValue({
      data: null,
      error: null,
      response: new Response(null, { status: 204 }),
    });

    render(wrap(<ContactDetailPanel contactId="c-1" onClose={() => {}} />));
    await waitFor(() => screen.getByRole('heading', { name: /Test Person/i }));

    await user.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const [path, opts] = postMock.mock.calls[0] as [string, { params: { path: { contact_id: string } } }];
    expect(path).toBe('/contacts/{contact_id}/archive');
    expect(opts.params.path.contact_id).toBe('c-1');
  });

  test('Unarchive button surfaces when contact is archived', async () => {
    getMock.mockImplementationOnce(async () => ({
      data: makeDetail({ archived_at: '2026-06-01T00:00:00Z' }),
      error: null,
      response: new Response(null, { status: 200 }),
    }));
    // Subsequent GETs (e.g. outreach) fall through to the default.
    getMock.mockImplementation(async (path: string) => {
      if (path === '/contacts/{contact_id}/outreach') {
        return {
          data: { total: 0, offset: 0, limit: 50, items: [] },
          error: null,
          response: new Response(null, { status: 200 }),
        };
      }
      return {
        data: makeDetail({ archived_at: '2026-06-01T00:00:00Z' }),
        error: null,
        response: new Response(null, { status: 200 }),
      };
    });

    render(wrap(<ContactDetailPanel contactId="c-1" onClose={() => {}} />));
    await waitFor(() => screen.getByRole('heading', { name: /Test Person/i }));

    expect(screen.getByTestId('archived-badge')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Unarchive' })).toBeInTheDocument();
  });

  test('Close button calls onClose', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(wrap(<ContactDetailPanel contactId="c-1" onClose={onClose} />));
    await waitFor(() => screen.getByRole('heading', { name: /Test Person/i }));

    await user.click(screen.getByRole('button', { name: /close detail panel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('shows error state when contact GET fails', async () => {
    getMock.mockImplementation(async () => ({
      data: null,
      error: { detail: 'not found' },
      response: new Response(null, { status: 404 }),
    }));

    render(wrap(<ContactDetailPanel contactId="c-1" onClose={() => {}} />));
    await waitFor(() => {
      // The error message text from the thrown openapi-fetch error
      // surfaces in the panel's error region.
      expect(screen.getByText(/failed to load contact/i)).toBeInTheDocument();
    });
  });
});
