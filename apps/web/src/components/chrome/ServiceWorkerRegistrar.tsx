'use client';

import { useEffect } from 'react';

/**
 * Registers ``/sw.js`` after first mount (PR feat/pwa-tier1-installable).
 *
 * Mounted from ``layout.tsx`` so every route in the app participates in
 * the install criterion. Runs in a useEffect so it never executes on
 * the server. Three guards:
 *
 *   1. ``'serviceWorker' in navigator`` — SSR-safe (window doesn't
 *      exist server-side; this is a client component so window does
 *      exist at runtime, but the property check also covers browsers
 *      that have explicitly disabled SWs).
 *   2. Development build (``process.env.NODE_ENV !== 'production'``)
 *      skips registration. Avoids the ``InvalidStateError`` flood that
 *      happens when Next's HMR + a registered SW collide, and avoids
 *      caching half-broken dev assets if a later PR adds caching.
 *   3. Errors are swallowed with a single console.warn. There is no
 *      user-facing toast: the SW is non-essential — the app continues
 *      to work without it; install-prompt eligibility is what's lost.
 *
 * Returns null. The component renders no DOM — its only effect is the
 * registration side-effect on mount.
 */
export function ServiceWorkerRegistrar(): null {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') {
      return;
    }
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
      return;
    }
    // ``scope: '/'`` is the default, restated for legibility. The SW
    // ships from the same origin as the app — required by the SW
    // spec; no cross-origin gymnastics needed.
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err: unknown) => {
      // Failure is non-fatal — log once for diagnosis.
      // eslint-disable-next-line no-console
      console.warn('ServiceWorker registration failed', err);
    });
  }, []);

  return null;
}
