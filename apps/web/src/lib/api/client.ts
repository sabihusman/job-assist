'use client';

import type { paths } from '@/lib/types/openapi';
import createClient from 'openapi-fetch';

/**
 * Base URL for the FastAPI backend. Defaults to localhost for dev; the
 * Vercel project overrides this via `NEXT_PUBLIC_API_BASE_URL` so preview
 * and prod hit Railway. Falls back to "" (same-origin) only as a last
 * resort — that's almost certainly wrong in v1 and worth a console nudge.
 */
const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

/**
 * Type-safe HTTP client. Path strings, path params, query params, and
 * response shapes are all inferred from the generated `paths` type, which
 * comes from `apps/api/openapi.json` (committed snapshot, regenerated on
 * every API merge — see `pnpm openapi:generate`).
 *
 * Example:
 *   const { data, error } = await api.GET("/postings", {
 *     params: { query: { limit: 20 } },
 *   });
 */
export const api = createClient<paths>({ baseUrl });

export { baseUrl as API_BASE_URL };
