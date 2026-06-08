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

  const init: RequestInit = {
    method,
    headers,
    redirect: 'manual',
  };
  if (hasBody) {
    // BUFFER the request body rather than streaming ``req.body`` upstream with
    // ``duplex: 'half'``. Forwarding a Web ReadableStream through undici fetch on
    // Vercel intermittently RESETS the connection on POST/PUT bodies — reads
    // (no body) worked, but every WRITE returned an opaque empty-body 500. The
    // direct-to-Railway curl proved the API/DB are fine; the streamed-body proxy
    // hop was the sole failure. Buffering sidesteps the broken streaming path
    // entirely and lets fetch set a correct Content-Length. Payloads here are
    // small JSON (and the occasional small resume upload), so buffering is safe.
    const buf = await req.arrayBuffer();
    if (buf.byteLength > 0) init.body = buf;
  }

  // Surface a proxy→upstream connection failure instead of letting it bubble
  // into an opaque empty-body 500. If the fetch to Railway is reset / hangs /
  // refuses (e.g. during a streamed write body), Next would otherwise return a
  // bare 500 with no body — undiagnosable. Return a 502 carrying the real error
  // (incl. undici's `cause`, where ECONNRESET / "socket hang up" lives).
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch (err) {
    const base = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
    const cause = (err as { cause?: unknown }).cause;
    const causeMsg =
      cause instanceof Error ? `${cause.name}: ${cause.message}` : cause ? String(cause) : '';
    return new Response(
      JSON.stringify({
        detail: 'Proxy could not reach the API',
        error: causeMsg ? `${base} (cause: ${causeMsg})` : base,
        upstream_path: upstreamPath,
        method,
      }),
      { status: 502, headers: { 'content-type': 'application/json' } },
    );
  }

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
