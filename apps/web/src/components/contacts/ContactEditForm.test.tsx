import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { ContactEditForm } from '@/components/contacts/ContactEditForm';
import type { ContactDetail } from '@/lib/contacts/types';

const { patchMock } = vi.hoisted(() => ({ patchMock: vi.fn() }));

vi.mock('@/lib/api/client', () => ({
  api: { GET: vi.fn(), POST: vi.fn(), PATCH: patchMock },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeContact(overrides: Partial<ContactDetail> = {}): ContactDetail {
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

function patchBody() {
  const [, opts] = patchMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
  return opts.body;
}

beforeEach(() => {
  patchMock.mockReset();
  patchMock.mockResolvedValue({
    data: makeContact(),
    error: null,
    response: new Response(null, { status: 200 }),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('ContactEditForm (fix/audit #6 — trim at submit, not per keystroke)', () => {
  test('typing preserves internal spaces (the per-keystroke trim ate them)', async () => {
    const user = userEvent.setup();
    render(wrap(<ContactEditForm contact={makeContact({ current_position: '' })} />));

    const input = screen.getByLabelText(/current position/i);
    await user.type(input, 'Vice President');

    // Before the fix, emptyToNull(.trim()) ran on every onChange, so the
    // space after "Vice" was stripped the instant it was typed and the
    // field collapsed to "VicePresident". The raw value must survive.
    expect(input).toHaveValue('Vice President');
  });

  test('submit trims leading/trailing whitespace into the PATCH body', async () => {
    const user = userEvent.setup();
    render(wrap(<ContactEditForm contact={makeContact({ current_position: 'Senior PM' })} />));

    const input = screen.getByLabelText(/current position/i);
    await user.clear(input);
    await user.type(input, '  Group PM  ');
    await user.click(screen.getByRole('button', { name: /save contact changes/i }));

    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(1));
    // Whitespace is normalized once, at submit time.
    expect(patchBody().current_position).toBe('Group PM');
  });

  test('clearing a field to blank sends an explicit null (clear)', async () => {
    const user = userEvent.setup();
    render(wrap(<ContactEditForm contact={makeContact({ current_position: 'Senior PM' })} />));

    const input = screen.getByLabelText(/current position/i);
    await user.clear(input);
    await user.click(screen.getByRole('button', { name: /save contact changes/i }));

    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(1));
    expect(patchBody().current_position).toBeNull();
  });

  test('a whitespace-only edit that matches the (null) original is not dirty', async () => {
    const user = userEvent.setup();
    render(wrap(<ContactEditForm contact={makeContact({ current_position: null })} />));

    const input = screen.getByLabelText(/current position/i);
    await user.type(input, '   ');
    // Normalizes to null === original null → no diff → Save stays disabled.
    expect(screen.getByRole('button', { name: /save contact changes/i })).toBeDisabled();
  });
});
