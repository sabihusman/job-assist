/**
 * Minimal service worker for PWA installability (feat/pwa-tier1-installable).
 *
 * Tier 1 only: this SW exists ONLY to satisfy Chrome's beforeinstallprompt
 * criterion, which requires a registered service worker with a fetch
 * handler that participates in at least one navigation request.
 *
 * Intentionally NOT here:
 *   - any cache.put / cache.match logic
 *   - precaching of any asset
 *   - background sync, push subscription, periodic sync
 *   - notification handlers
 *   - skipWaiting / clients.claim
 *
 * The handler below is a deliberate passthrough — every fetch is
 * forwarded to the network unchanged. There is no offline behavior. If
 * later PRs add offline-Triage or push, they extend this file; today
 * extending it is out of scope.
 *
 * Versioning: when this file changes, bump SW_VERSION below so the
 * browser picks up the new SW on the next page load. The string isn't
 * used anywhere — its only purpose is to force a byte-difference that
 * triggers the standard SW update flow.
 */
const SW_VERSION = 'tier1-v1';

self.addEventListener('install', () => {
  // Activate immediately — there is no precache step, so the standard
  // "waiting" phase serves no purpose and just delays the install
  // prompt becoming eligible.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Claim open pages so the new SW controls them without a reload.
  // Cheap; only meaningful when SW_VERSION bumps.
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Only handle top-level navigation requests. Subresources (CSS / JS /
  // images / API calls) go to the network on their own path — leaving
  // them unhandled keeps the SW out of the request lifecycle for
  // anything that isn't a page load, which is the install-criterion
  // minimum.
  if (event.request.mode !== 'navigate') {
    return;
  }
  event.respondWith(fetch(event.request));
});

// SW_VERSION is referenced once to keep the linter quiet about an
// unused constant when this file is the entire "module."
self.SW_VERSION = SW_VERSION;
