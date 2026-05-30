import { expect, test } from '@playwright/test';

/**
 * E2E smoke for the PWA Tier 1 install surface (feat/pwa-tier1-installable).
 *
 * Three scenarios — each catches one common regression that silently
 * breaks installability without surfacing in a unit test:
 *
 *   1. ``<link rel="manifest">`` is emitted in the document head and
 *      points to the route Next 15's ``manifest.ts`` route serves.
 *   2. The manifest URL returns parseable JSON with the
 *      installability-critical fields (defense-in-depth over the
 *      Vitest manifest-shape test, which runs against the function
 *      output but doesn't exercise the actual route).
 *   3. The static assets the manifest references — icon-192,
 *      icon-512, apple-touch-icon — return 200.
 *
 * The service-worker registration itself is dev-mode-disabled (see
 * ServiceWorkerRegistrar.tsx), so we don't test it under Playwright
 * (which runs against the dev server in this repo). SW activation
 * gets exercised in production manually.
 */

test('document head links the manifest', async ({ page }) => {
  await page.goto('/');
  const href = await page
    .locator('link[rel="manifest"]')
    .first()
    .getAttribute('href');
  // Next's metadata API canonicalises to ``/manifest.webmanifest`` for
  // the ``manifest.ts`` route. Accept either that or a fallback path
  // — both are valid manifest links.
  expect(href).toMatch(/manifest(\.webmanifest|\.json)?$/);
});

test('manifest endpoint returns installability-critical fields', async ({ page }) => {
  // Read the actual href off the page rather than hard-coding the
  // route — keeps this test honest if Next changes its canonical path.
  await page.goto('/');
  const href = await page
    .locator('link[rel="manifest"]')
    .first()
    .getAttribute('href');
  expect(href).not.toBeNull();
  const resp = await page.request.get(href!);
  expect(resp.status()).toBe(200);
  const manifest = await resp.json();
  expect(manifest.name).toBe('Job Assist');
  expect(manifest.display).toBe('standalone');
  expect(manifest.start_url).toBe('/');
  expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
  expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  const sizes: string[] = (manifest.icons ?? []).map((i: { sizes: string }) => i.sizes);
  expect(sizes).toContain('192x192');
  expect(sizes).toContain('512x512');
});

test('icon assets served from /public are reachable', async ({ page }) => {
  await page.goto('/');
  for (const path of ['/icon-192.png', '/icon-512.png', '/apple-touch-icon.png']) {
    const resp = await page.request.get(path);
    expect(resp.status(), `${path} returned ${resp.status()}`).toBe(200);
    const contentType = resp.headers()['content-type'] ?? '';
    expect(contentType).toContain('image/png');
  }
});
