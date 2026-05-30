/**
 * Unit test for the PWA manifest (PR feat/pwa-tier1-installable).
 *
 * Pins the required fields for Chrome's installability criterion. If
 * any of these drift (e.g. ``display`` changes to "browser",
 * ``start_url`` loses its leading slash, or an icon size is dropped)
 * the install prompt silently stops appearing — this test fails first.
 */

import { describe, expect, it } from 'vitest';

import manifest from './manifest';

describe('manifest', () => {
  const m = manifest();

  it('exposes name + short_name', () => {
    expect(m.name).toBe('Job Assist');
    expect(m.short_name).toBe('Job Assist');
  });

  it('uses standalone display + root start_url for installability', () => {
    expect(m.display).toBe('standalone');
    expect(m.start_url).toBe('/');
  });

  it('declares both theme_color and background_color', () => {
    // The hex values themselves are derived from globals.css tokens;
    // pin the SHAPE (#RRGGBB) so a refactor to ``oklch(...)`` (which
    // some browsers reject in the manifest) fails this test.
    expect(m.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(m.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('ships maskable 192 + 512 PNG icons', () => {
    const icons = m.icons ?? [];
    const sizes = icons.map((i) => i.sizes);
    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');
    for (const icon of icons) {
      expect(icon.type).toBe('image/png');
      // ``maskable any`` lets one PNG cover both the legacy + Android
      // maskable-mask use cases. Either word may appear in the
      // space-separated string.
      expect(icon.purpose).toContain('maskable');
      expect(icon.purpose).toContain('any');
      expect(icon.src.startsWith('/')).toBe(true);
    }
  });
});
