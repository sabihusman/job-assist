import { type Page, type Route, expect } from '@playwright/test';

/**
 * Conventions for E2E tests in this project.
 *
 * Failures across PRs #32a / #32b / #32c clustered into three classes,
 * and the first two are structurally fixable here:
 *
 *   1. **Race conditions** — assertion fires before mocked data lands.
 *      Use `waitForDataReady` after navigating to a data-driven page.
 *   2. **Selector ambiguity** — sidebar nav labels ("Applied", "Triage",
 *      …) collide with page-body content. Use `mainContent(page)` to
 *      scope queries to the page's main region (AppShell wraps the
 *      content in `<main aria-label="Page content">`).
 *   3. **Stale tests after contract changes** — not preventable; keep
 *      tests aligned with the code.
 *
 * Also exports the canonical `mockApi` so each spec file doesn't
 * re-declare the boilerplate route handlers.
 */

// ── Scope helpers ────────────────────────────────────────────────────────

/** Scope a query to the AppShell `<main aria-label="Page content">`. */
export function mainContent(page: Page) {
  return page.getByRole('main', { name: /page content/i });
}

/** Scope to the Sidebar `<aside aria-label="Primary navigation">`. */
export function sidebar(page: Page) {
  return page.getByRole('complementary', { name: /primary navigation/i });
}

// ── Wait helpers ─────────────────────────────────────────────────────────

/**
 * Wait until first-load skeletons disappear (animate-pulse on the
 * page content). Run right after `page.goto(...)` and before
 * assertions that depend on fetched data.
 *
 * Times out fast (3s) — if a real test has long-running data the
 * caller should adjust. The intent is to bridge the gap between the
 * route mock resolving and React committing the data render.
 */
export async function waitForDataReady(page: Page, options: { timeout?: number } = {}) {
  const { timeout = 3000 } = options;
  // Wait until no animate-pulse skeletons remain inside <main>. The
  // selector is intentionally loose — every skeleton we ship uses the
  // tailwind `animate-pulse` utility.
  await expect(
    mainContent(page).locator('.animate-pulse'),
    'data skeleton should clear after fetch',
  ).toHaveCount(0, { timeout });
}

// ── Mock API ─────────────────────────────────────────────────────────────

/**
 * Fixture dataset used by the pages.spec.ts suite. Override individual
 * fixtures via the `overrides` argument; everything else falls back to
 * the defaults below.
 */
export type MockFixtures = Partial<{
  postings: unknown;
  outcomes: unknown;
  companies: unknown;
  calibration: unknown;
}>;

const NOW_ISO = () => new Date().toISOString();

const DEFAULT_FIXTURES = {
  postings: {
    total: 0,
    offset: 0,
    limit: 20,
    items: [] as unknown[],
  },
  outcomes: {
    total: 0,
    offset: 0,
    limit: 2000,
    items: [] as unknown[],
  },
  companies: {
    total: 0,
    offset: 0,
    limit: 100,
    items: [] as unknown[],
  },
  calibration: {
    window: { since: NOW_ISO(), until: NOW_ISO() },
    surfaced: 0,
    interested: 0,
    interested_rate: null,
    applied: 0,
    rejected_by_you: 0,
    top_rejected_role_families: [] as unknown[],
  },
};

/**
 * Install network mocks for the four common endpoints. Tests just need
 *
 *     await mockApi(page, { postings: { items: [...] } });
 *
 * The catch-all `**` patterns match both the local dev URL and the
 * Vercel preview build's Railway URL — Playwright globs work on the
 * full URL.
 */
export async function mockApi(page: Page, overrides: MockFixtures = {}) {
  const fixtures = { ...DEFAULT_FIXTURES, ...overrides };

  await page.route('**/postings*', async (route: Route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const url = route.request().url();
    // Don't match /postings/{id} or /postings/{id}/state — those are
    // separate routes handled below.
    if (/\/postings\/[^/?]+(?:\?|$)/.test(url)) return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtures.postings),
    });
  });

  await page.route('**/outcomes*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtures.outcomes),
    });
  });

  await page.route('**/companies*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtures.companies),
    });
  });

  await page.route('**/stats/calibration*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtures.calibration),
    });
  });
}
