import type { MetadataRoute } from 'next';

/**
 * PWA web manifest (PR feat/pwa-tier1-installable).
 *
 * Tier 1 = installable + standalone window only. Specifically NOT:
 *   * offline caching (no Workbox/Serwist runtime)
 *   * push notifications
 *   * background sync
 *
 * Theme + background colors are the hex equivalents of the light-mode
 * design tokens in ``src/app/globals.css``:
 *   * --primary    : oklch(60% 0.11 215) ≈ #3b8fa9 (theme color — used
 *                    by the browser for the standalone-window chrome
 *                    and the install-prompt accent)
 *   * --background : oklch(98.5% 0.003 95) ≈ #fbf9f4 (background color
 *                    — used for the launch splash on Android)
 *
 * The two PNG icons advertise ``purpose: "maskable any"`` so a single
 * file covers both the legacy "any" use (iOS Safari, older browsers)
 * and Android's maskable-icon cropping. The letterforms sit inside the
 * central 64% of the canvas (see ``scripts/generate-icons.py``) so the
 * Android safe-zone crop never clips them.
 *
 * Next 15 serves this route at ``/manifest.webmanifest`` with the
 * correct ``Content-Type: application/manifest+json`` and emits the
 * ``<link rel="manifest">`` tag automatically from the document head.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Job Assist',
    short_name: 'Job Assist',
    description: 'Personal job-search aggregation and triage',
    start_url: '/',
    display: 'standalone',
    orientation: 'portrait',
    theme_color: '#3b8fa9',
    background_color: '#fbf9f4',
    icons: [
      {
        src: '/icon-192.png',
        sizes: '192x192',
        type: 'image/png',
        // Next's MetadataRoute.Manifest typing only enumerates single
        // values (``'any' | 'maskable' | 'monochrome'``), but the W3C
        // manifest spec explicitly allows a space-separated list. The
        // cast widens the type to what the spec — and Chrome /
        // Android — actually accept.
        purpose: 'maskable any' as 'any',
      },
      {
        src: '/icon-512.png',
        sizes: '512x512',
        type: 'image/png',
        // Next's MetadataRoute.Manifest typing only enumerates single
        // values (``'any' | 'maskable' | 'monochrome'``), but the W3C
        // manifest spec explicitly allows a space-separated list. The
        // cast widens the type to what the spec — and Chrome /
        // Android — actually accept.
        purpose: 'maskable any' as 'any',
      },
    ],
  };
}
