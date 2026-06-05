import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { StatusButtons } from '@/components/triage/StatusButtons';

// sonner toasts are noise here.
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const fetchMock = vi.fn();

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const PID = 'posting-123';

describe('StatusButtons', () => {
  test('renders all five lifecycle stages', () => {
    wrap(<StatusButtons postingId={PID} current={null} />);
    for (const label of ['Applied', 'Interview', 'Offer', 'Accepted', 'Rejected']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  test('clicking a stage PUTs {status} to the proxy', async () => {
    const user = userEvent.setup();
    wrap(<StatusButtons postingId={PID} current="applied" />);

    await user.click(screen.getByRole('button', { name: 'Interview' }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/be/postings/posting-123/status');
    expect(init.method).toBe('PUT');
    expect((init.headers as Record<string, string>)['content-type']).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({ status: 'interview' });
  });

  test('marks the current stage as pressed', () => {
    wrap(<StatusButtons postingId={PID} current="offer" />);
    expect(screen.getByRole('button', { name: 'Offer' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Applied' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  test('shows the Gmail-rejection hint (informational) with the company name', () => {
    wrap(
      <StatusButtons postingId={PID} current="applied" companyName="Athene" gmailRejectionHint />,
    );
    expect(screen.getByText(/gmail saw a rejection from athene/i)).toBeInTheDocument();
    expect(screen.getByText(/informational only/i)).toBeInTheDocument();
  });

  test('omits the hint when there is no Gmail rejection', () => {
    wrap(<StatusButtons postingId={PID} current="applied" gmailRejectionHint={false} />);
    expect(screen.queryByText(/gmail saw a rejection/i)).not.toBeInTheDocument();
  });
});
