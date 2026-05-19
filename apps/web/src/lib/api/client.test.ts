import createClient from 'openapi-fetch';
import { describe, expect, test, vi } from 'vitest';

import type { paths } from '@/lib/types/openapi';

/**
 * openapi-fetch captures the `fetch` reference at client-creation time,
 * so a global spy installed after import is too late. Instead we
 * construct a fresh client per-test with an injected fetch and assert
 * on the URL openapi-fetch hands to it.
 *
 * Coverage: openapi-fetch composes baseUrl + path + serialized query
 * params correctly for the `GET /postings` shape #32b will rely on.
 */
describe('api client', () => {
  test('GET /postings hits the configured base URL with a serialized query', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ total: 0, offset: 0, limit: 20, items: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    const client = createClient<paths>({
      baseUrl: 'http://api.example.test',
      fetch: fetchMock,
    });

    await client.GET('/postings', { params: { query: { limit: 20, offset: 0 } } });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error('fetch was not called');
    const arg = call[0];
    // openapi-fetch passes a `Request` instance — `.toString()` returns
    // "[object Request]" in jsdom, so reach for `.url` directly.
    const url =
      typeof arg === 'string' ? arg : arg instanceof URL ? arg.toString() : (arg as Request).url;
    expect(url).toContain('http://api.example.test');
    expect(url).toContain('/postings');
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=0');
  });
});
