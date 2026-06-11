import { type Route, expect, test } from '@playwright/test';

/**
 * Mobile-viewport regression spec (fix/mobile-card-title).
 *
 * Bug: on narrow viewports the triage card title collapsed to a single
 * clipped character. The hover-revealed action column is opacity-0 (no
 * paint) but was still IN LAYOUT (~240px for four buttons) — on a ~400px
 * viewport it, plus the 72px score rail, starved the min-w-0 title to zero
 * width. On touch there's no hover, so the column could never be revealed
 * there anyway (and its invisible buttons were tappable — an
 * accidental-Pass hazard).
 *
 * Fix under test: the action column is ``hidden md:flex`` and the title
 * truncates with ellipsis at the available width.
 *
 * The sidebar pref is seeded COLLAPSED via localStorage before first paint —
 * the realistic mobile state (and the documented AppShell first-paint gap at
 * ~380px is out of scope here, same stance as the PR #57 note in
 * triage.spec.ts).
 */

const LONG_TITLE = 'Senior Product Manager, Payments Platform & Partner Integrations';

const POSTING = {
  id: 'p-mobile',
  company: { id: 'c-m', name: 'MobileCo', domain: null, description: null, tier: 2 },
  role: {
    title: LONG_TITLE,
    family: 'product_management',
    department: null,
    team: null,
    seniority: 'senior_pm',
  },
  location_raw: 'Remote',
  locations_normalized: ['Remote'],
  remote_type: 'remote',
  salary: null,
  source: { ats: 'greenhouse', url: 'https://example.test/jd/m' },
  first_seen_at: new Date().toISOString(),
  score: 77,
  state: { current: null, reason: null, snooze_until: null, current_at: null },
};

test.use({ viewport: { width: 400, height: 800 } });

test.beforeEach(async ({ page }) => {
  // Realistic mobile state: sidebar pref rehydrates collapsed (zustand
  // persist key — see lib/stores/ui.ts).
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'job-assist:ui',
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 }),
    );
  });
  await page.route('**/postings*', async (route: Route) => {
    if (route.request().method() !== 'GET') return route.continue();
    if (/\/postings\/[^/?]+(?:\?|$)/.test(route.request().url())) return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: 1, offset: 0, limit: 20, items: [POSTING] }),
    });
  });
  await page.route('**/postings/*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...POSTING,
        description_markdown: 'JD body',
        jd_summary_markdown: null,
        division: null,
        posted_at: null,
        last_seen_at: null,
        closed_at: null,
        state_history: [],
      }),
    });
  });
  await page.route('**/stats/calibration*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        window: { since: new Date().toISOString(), until: new Date().toISOString() },
        surfaced: 1,
        interested: 0,
        interested_rate: 0,
        applied: 0,
        rejected_by_you: 0,
        top_rejected_role_families: [],
      }),
    });
  });
});

test('400px: card title gets real width and truncates (no single-char collapse)', async ({
  page,
}) => {
  await page.goto('/');
  const title = page.getByTitle(LONG_TITLE);
  await expect(title).toBeVisible();
  const box = await title.boundingBox();
  // Pre-fix the starved title measured ~0-12px (one clipped character).
  // Post-fix it owns the card's flexible column — comfortably >100px even
  // with the score rail present.
  expect(box).not.toBeNull();
  expect(box?.width ?? 0).toBeGreaterThan(100);
});

test('400px: the hover action column is OUT OF LAYOUT (hidden, not just transparent)', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByTitle(LONG_TITLE)).toBeVisible();
  // display:none below md — toBeHidden() distinguishes from the old
  // opacity-0 state, which Playwright counts as visible.
  await expect(page.getByRole('toolbar', { name: 'Actions' })).toBeHidden();
});

test('400px: score rail and metadata still render alongside the title', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('score-block').first()).toHaveText('77');
  await expect(page.getByText('MobileCo').first()).toBeVisible();
});
