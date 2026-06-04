'use client';

import type { paths } from '@/lib/types/openapi';
import createClient from 'openapi-fetch';

/**
 * Base URL for the FastAPI backend.
 *
 * feat/frontend-api-proxy: all calls go through the SAME-ORIGIN Next.js proxy
 * (`app/api/be/[...path]`), which injects the server-side bearer token — the
 * token never reaches the browser. The proxy reads the real Railway origin
 * from a server-only env var; the browser only ever sees `/api/be`. The export
 * anchor in ExportButton.tsx uses this same base, so it routes through the
 * proxy too and still streams the xlsx download.
 */
const baseUrl = '/api/be';

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
