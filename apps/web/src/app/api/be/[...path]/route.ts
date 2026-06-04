import type { NextRequest } from 'next/server';

/**
 * Server-side API proxy (feat/frontend-api-proxy).
 *
 * Every browser API call goes to this same-origin catch-all instead of hitting
 * Railway directly, so the shared bearer token can be injected HERE from a
 * server-only env var (``API_AUTH_TOKEN``, NOT ``NEXT_PUBLIC_*``) — the token
 * never reaches the browser. The client's openapi-fetch baseUrl points at
 * ``/api/be`` (see ``lib/api/client.ts``); the export anchor points at
 * ``/api/be/postings/export.xlsx``.
 *
 * Forwards method + path + query + body upstream and streams the response back
 * unchanged (status, Content-Type, and Content-Disposition — so the xlsx export
 * still downloads natively).
 */

// Node runtime: streams request/response bodies and reads server-only env.
// Never statically cached — this is a live proxy.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The Railway origin is not itself secret (the TOKEN is); reuse the existing
// NEXT_PUBLIC_API_BASE_URL as the upstream target, overridable via a dedicated
// server var. Read per-request (not module-level) so it's test-injectable and
// always reflects the runtime env.
function upstreamTarget(): string {
  const raw =
    process.env.API_PROXY_TARGET ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
  return raw.replace(/\/+$/, '');
}

// SERVER-ONLY bearer token. Injected here; never serialized to the browser.
function authToken(): string {
  return process.env.API_AUTH_TOKEN ?? '';
}

// Hop-by-hop / host headers that must not be forwarded upstream.
const STRIP_REQUEST_HEADERS = new Set(['host', 'connection', 'content-length']);
// Response headers that ``fetch`` already resolved — re-emitting them corrupts
// the streamed body.
const STRIP_RESPONSE_HEADERS = new Set(['content-encoding', 'content-length', 'transfer-encoding']);

async function handler(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
  const { path } = await ctx.params;
  const upstreamPath = (path ?? []).join('/');
  const url = `${upstreamTarget()}/${upstreamPath}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIP_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  const token = authToken();
  if (token) headers.set('authorization', `Bearer ${token}`);

  const method = req.method.toUpperCase();
  const hasBody = method !== 'GET' && method !== 'HEAD';

  const init: RequestInit & { duplex?: 'half' } = {
    method,
    headers,
    redirect: 'manual',
  };
  if (hasBody) {
    init.body = req.body as BodyInit;
    // undici requires duplex when streaming a request body.
    init.duplex = 'half';
  }

  const upstream = await fetch(url, init);

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export {
  handler as GET,
  handler as POST,
  handler as PUT,
  handler as PATCH,
  handler as DELETE,
  handler as HEAD,
  handler as OPTIONS,
};
