import type { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { GET, POST } from '@/app/api/be/[...path]/route';

/**
 * feat/frontend-api-proxy — server-side proxy route handler tests.
 *
 * The proxy forwards method/path/query/body upstream, injects the server-only
 * bearer token, and streams the response back (incl. Content-Disposition for
 * the xlsx export download).
 */

const fetchMock = vi.fn();

function mockReq(opts: {
  method?: string;
  search?: string;
  headers?: Record<string, string>;
  body?: unknown;
}): NextRequest {
  return {
    method: opts.method ?? 'GET',
    headers: new Headers(opts.headers ?? {}),
    nextUrl: { search: opts.search ?? '' },
    body: opts.body ?? null,
    // The proxy now BUFFERS the body via req.arrayBuffer() (instead of streaming
    // req.body with duplex) — provide it.
    arrayBuffer: async () => {
      const b = opts.body;
      if (b == null) return new ArrayBuffer(0);
      if (typeof b === 'string') return new TextEncoder().encode(b).buffer as ArrayBuffer;
      return b as ArrayBuffer;
    },
  } as unknown as NextRequest;
}

function params(path: string[]): { params: Promise<{ path?: string[] }> } {
  return { params: Promise.resolve({ path }) };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  process.env.API_PROXY_TARGET = 'http://api.test';
  process.env.API_AUTH_TOKEN = 'secret-token';
});

afterEach(() => {
  vi.unstubAllGlobals();
  process.env.API_PROXY_TARGET = undefined;
  process.env.API_AUTH_TOKEN = undefined;
});

describe('API proxy route', () => {
  test('forwards a GET with query and injects the server-side bearer token', async () => {
    fetchMock.mockResolvedValue(new Response('[]', { status: 200 }));

    await GET(mockReq({ search: '?state=triage&limit=1' }), params(['postings']));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://api.test/postings?state=triage&limit=1');
    expect((init.headers as Headers).get('authorization')).toBe('Bearer secret-token');
    // GET carries no body/duplex.
    expect(init.body).toBeUndefined();
  });

  test('the export download streams through with Content-Disposition preserved', async () => {
    const upstream = new Response('XLSX_BYTES', {
      status: 200,
      headers: {
        'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'content-disposition': 'attachment; filename="triage-export.xlsx"',
      },
    });
    fetchMock.mockResolvedValue(upstream);

    const res = await GET(
      mockReq({ search: '?state=triage' }),
      params(['postings', 'export.xlsx']),
    );

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://api.test/postings/export.xlsx?state=triage');
    expect(res.status).toBe(200);
    // The Content-Disposition + Content-Type must survive so the browser
    // downloads the file natively.
    expect(res.headers.get('content-disposition')).toBe(
      'attachment; filename="triage-export.xlsx"',
    );
    expect(res.headers.get('content-type')).toContain('spreadsheetml.sheet');
    expect(await res.text()).toBe('XLSX_BYTES');
  });

  test('forwards a POST by BUFFERING the body (no duplex streaming)', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));

    await POST(
      mockReq({ method: 'POST', body: '[{"name":"X","tier":4}]' }),
      params(['admin', 'companies', 'crawl-config']),
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { duplex?: string }];
    expect(url).toBe('http://api.test/admin/companies/crawl-config');
    expect(init.method).toBe('POST');
    // The body is BUFFERED (ArrayBuffer), not the raw stream — and no duplex.
    // Streaming req.body upstream is what reset write POSTs on Vercel.
    expect(init.duplex).toBeUndefined();
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('[{"name":"X","tier":4}]');
  });

  test('does not set an Authorization header when the token is unconfigured', async () => {
    process.env.API_AUTH_TOKEN = '';
    fetchMock.mockResolvedValue(new Response('[]', { status: 200 }));

    await GET(mockReq({}), params(['postings']));

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).has('authorization')).toBe(false);
  });

  test('strips the inbound host header before forwarding upstream', async () => {
    fetchMock.mockResolvedValue(new Response('[]', { status: 200 }));

    await GET(mockReq({ headers: { host: 'evil.example', 'x-keep': '1' } }), params(['postings']));

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).has('host')).toBe(false);
    expect((init.headers as Headers).get('x-keep')).toBe('1');
  });

  test('strips the Expect header (undici fetch rejects it — broke every write)', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));

    await POST(
      mockReq({ method: 'POST', body: '[]', headers: { expect: '100-continue', 'x-keep': '1' } }),
      params(['admin', 'companies', 'crawl-config']),
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).has('expect')).toBe(false);
    expect((init.headers as Headers).get('x-keep')).toBe('1');
  });

  test('upstream connection failure → 502 carrying the real error (not an empty 500)', async () => {
    // undici surfaces connection resets as `TypeError: fetch failed` with the
    // real reason on `.cause`. The proxy must echo BOTH so a write that dies on
    // the proxy→API hop is diagnosable instead of an opaque empty body.
    const err = new TypeError('fetch failed');
    (err as { cause?: unknown }).cause = new Error('read ECONNRESET');
    fetchMock.mockRejectedValue(err);

    const res = await POST(
      mockReq({ method: 'POST', body: 'BODY_STREAM' }),
      params(['admin', 'companies', 'crawl-config']),
    );

    expect(res.status).toBe(502);
    const body = (await res.json()) as { detail: string; error: string; upstream_path: string };
    expect(body.detail).toContain('could not reach');
    expect(body.error).toContain('fetch failed');
    expect(body.error).toContain('ECONNRESET');
    expect(body.upstream_path).toBe('admin/companies/crawl-config');
  });
});
