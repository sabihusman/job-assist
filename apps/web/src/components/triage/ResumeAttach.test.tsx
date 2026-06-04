import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { ResumeAttach } from '@/components/triage/ResumeAttach';
import type { ApplicationResumeMeta } from '@/lib/triage/types';

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

describe('ResumeAttach', () => {
  test('shows "None attached" and the upload + paste controls when empty', () => {
    wrap(<ResumeAttach postingId={PID} resume={null} />);
    expect(screen.getByText(/none attached/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/upload \.docx/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/paste text/i)).toBeInTheDocument();
  });

  test('uploading a file POSTs the bytes to the proxy with filename', async () => {
    const user = userEvent.setup();
    wrap(<ResumeAttach postingId={PID} resume={null} />);

    const file = new File(['resume bytes'], 'betterment.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    await user.upload(screen.getByLabelText(/upload \.docx/i), file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/be/postings/posting-123/resume?filename=betterment.docx');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(file);
  });

  test('pasting text + Save POSTs JSON resume_text', async () => {
    const user = userEvent.setup();
    wrap(<ResumeAttach postingId={PID} resume={null} />);

    await user.type(screen.getByLabelText(/paste text/i), 'my pasted resume');
    await user.click(screen.getByRole('button', { name: /save text/i }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/be/postings/posting-123/resume');
    expect((init.headers as Record<string, string>)['content-type']).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({ resume_text: 'my pasted resume' });
  });

  test('renders a download link through the proxy when a file is attached', () => {
    const resume: ApplicationResumeMeta = {
      has_file: true,
      file_name: 'attached.pdf',
      content_type: 'application/pdf',
      resume_text: null,
      angle: null,
      label: null,
      updated_at: '2026-06-14T00:00:00Z',
    };
    wrap(<ResumeAttach postingId={PID} resume={resume} />);
    const link = screen.getByTestId('resume-download') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/api/be/postings/posting-123/resume');
    expect(link.hasAttribute('download')).toBe(true);
    expect(link.textContent).toBe('attached.pdf');
  });
});
