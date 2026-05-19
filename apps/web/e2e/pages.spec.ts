import { expect, test } from '@playwright/test';

import { mainContent, mockApi, waitForDataReady } from './helpers';

/**
 * E2E coverage for the four pages shipped in PR #32c.
 *
 * Conventions (see e2e/helpers.ts):
 *   - `mockApi(page, { … })` installs network mocks for the common
 *     endpoints; pass per-test fixtures for the ones the test cares
 *     about.
 *   - `waitForDataReady(page)` runs after `page.goto(...)` to bridge
 *     the gap between the mocked fetch resolving and the React commit.
 *   - `mainContent(page)` scopes queries so chrome-region labels
 *     (sidebar "Applied", banner title, etc.) don't collide with page
 *     content.
 */

const NOW = new Date();
const recentIso = (daysAgo: number) =>
  new Date(NOW.getTime() - daysAgo * 86_400_000).toISOString();

const APPLIED_POSTINGS = {
  total: 2,
  offset: 0,
  limit: 500,
  items: [
    {
      id: 'p-alpha',
      company: { id: 'c-1', name: 'Alpha Co', domain: null, description: null, tier: 1 },
      role: {
        title: 'Senior PM, Alpha',
        family: 'product_management',
        department: null,
        team: null,
        seniority: 'senior_pm',
      },
      location_raw: 'Remote',
      locations_normalized: ['Remote'],
      remote_type: 'remote',
      salary: { min: 200000, max: 240000, currency: 'USD', period: 'annual' },
      source: { ats: 'greenhouse', url: null },
      first_seen_at: recentIso(7),
      score: null,
      state: { current: 'applied', reason: null, snooze_until: null, current_at: recentIso(7) },
    },
    {
      id: 'p-beta',
      company: { id: 'c-2', name: 'Beta Co', domain: null, description: null, tier: 2 },
      role: {
        title: 'Senior PM, Beta',
        family: 'product_management',
        department: null,
        team: null,
        seniority: 'senior_pm',
      },
      location_raw: null,
      locations_normalized: [],
      remote_type: 'hybrid',
      salary: null,
      source: { ats: 'lever', url: null },
      first_seen_at: recentIso(3),
      score: null,
      state: { current: 'applied', reason: null, snooze_until: null, current_at: recentIso(3) },
    },
  ],
};

const OUTCOMES = {
  total: 1,
  offset: 0,
  limit: 2000,
  items: [
    {
      id: 'o-1',
      posting_id: 'p-alpha',
      received_at: recentIso(2),
      stage: 'recruiter_screen_invite',
      confidence: 0.9,
    },
  ],
};

const COMPANIES = {
  total: 2,
  offset: 0,
  limit: 100,
  items: [
    {
      id: 'c-1',
      name: 'Alpha Co',
      domain: 'alpha.com',
      description: null,
      tier: 1,
      ats_set: ['greenhouse'],
      active_postings: 3,
      total_postings: 10,
    },
    {
      id: 'c-2',
      name: 'Beta Co',
      domain: null,
      description: null,
      tier: 2,
      ats_set: ['lever'],
      active_postings: 1,
      total_postings: 2,
    },
  ],
};

const CALIBRATION = {
  window: { since: recentIso(7), until: NOW.toISOString() },
  surfaced: 42,
  interested: 13,
  interested_rate: 0.31,
  applied: 2,
  rejected_by_you: 5,
  top_rejected_role_families: [] as unknown[],
};

test.beforeEach(async ({ page }) => {
  await mockApi(page, {
    postings: APPLIED_POSTINGS,
    outcomes: OUTCOMES,
    companies: COMPANIES,
    calibration: CALIBRATION,
  });
});

// ── Applied ─────────────────────────────────────────────────────────────

test('Applied page loads and renders one row per applied posting', async ({ page }) => {
  await page.goto('/applied');
  await waitForDataReady(page);
  const content = mainContent(page);
  await expect(content.getByText('Alpha Co')).toBeVisible();
  await expect(content.getByText('Beta Co')).toBeVisible();
});

test('Applied row expand reveals TIMELINE label', async ({ page }) => {
  await page.goto('/applied');
  await waitForDataReady(page);
  await mainContent(page).getByRole('button', { expanded: false }).first().click();
  await expect(mainContent(page).getByText(/timeline/i).first()).toBeVisible();
});

test('Applied sort=tier reorders the URL', async ({ page }) => {
  await page.goto('/applied');
  await waitForDataReady(page);
  // The AppliedRow chevron buttons have accessible names like
  // "Tier 1 Alpha Co Senior PM, …" (the Tier badge's aria-label
  // contributes a "Tier 1" substring). `name: 'tier'` would match
  // those rows too. Use `exact: true` against the sort strip's
  // visible lowercase "tier" button instead.
  await mainContent(page)
    .getByRole('button', { name: 'tier', exact: true })
    .click();
  await expect(page).toHaveURL(/sort=tier/);
});

// ── Pipeline ────────────────────────────────────────────────────────────

test('Pipeline page renders 8 stage columns in order', async ({ page }) => {
  await page.goto('/pipeline');
  await waitForDataReady(page);
  const content = mainContent(page);
  for (const label of ['APPLIED', 'RECRUITER', 'PHONE', 'VIDEO', 'ONSITE', 'OFFER', 'REJECTED', 'GHOSTED']) {
    await expect(content.getByText(label, { exact: true })).toBeVisible();
  }
});

test('Pipeline buckets the alpha posting into RECRUITER (latest outcome)', async ({ page }) => {
  await page.goto('/pipeline');
  await waitForDataReady(page);
  const recruiter = mainContent(page).getByRole('region', { name: /recruiter screen/i });
  await expect(recruiter.getByText('Alpha Co')).toBeVisible();
});

// ── Companies ───────────────────────────────────────────────────────────

// Override the helper's glob with a regex route. The helper's
// `**/companies*` pattern appears to work for /postings, /outcomes,
// /stats — but for /companies the response either doesn't reach the
// page or fails to populate. A regex pattern bypasses any glob
// ambiguity and pins the mock unambiguously.
async function mockCompaniesDirect(page: import('@playwright/test').Page) {
  await page.route(/\/companies(\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(COMPANIES),
    });
  });
}

test('Companies table shows column headers and company rows', async ({ page }) => {
  await mockCompaniesDirect(page);
  await page.goto('/companies');
  // Query the whole page first — if data lands anywhere we know the
  // mock is firing; scope refinements come later.
  try {
    await expect(page.getByText('Alpha Co').first()).toBeVisible({ timeout: 10_000 });
  } catch (e) {
    // Dump body markup on failure so the trace tells us whether the
    // table rendered at all vs. the page is stuck on EmptyState.
    // eslint-disable-next-line no-console
    console.log('--- Companies page body ---\n', await page.locator('body').innerHTML());
    throw e;
  }
  for (const header of ['Name', 'Tier', 'ATS', 'Open', 'Applied', 'Outcomes']) {
    await expect(page.locator('th').getByText(header, { exact: true })).toBeVisible();
  }
  expect(await page.locator('th').getByText(/^Notes$/, { exact: true }).count()).toBe(0);
});

test('Companies subtitle reports target count', async ({ page }) => {
  await mockCompaniesDirect(page);
  await page.goto('/companies');
  await expect(page.getByText(/2 target companies/)).toBeVisible({ timeout: 10_000 });
});

// ── Stats ───────────────────────────────────────────────────────────────

test('Stats page renders the KPI grid and funnel section', async ({ page }) => {
  await page.goto('/stats');
  await waitForDataReady(page);
  const content = mainContent(page);
  await expect(content.getByText(/postings ingested \(7d\)/i)).toBeVisible();
  await expect(content.getByText(/outcome funnel/i)).toBeVisible();
});

test('Stats funnel shows all 6 stage labels', async ({ page }) => {
  await page.goto('/stats');
  await waitForDataReady(page);
  // Scope to the OUTCOME FUNNEL section. The sidebar nav has an
  // "Applied" link that would otherwise collide with the funnel row.
  const funnel = mainContent(page)
    .locator('section')
    .filter({ has: page.getByText(/outcome funnel/i) })
    .getByRole('list');
  for (const label of ['Applied', 'Recruiter screen', 'Phone interview', 'Video interview', 'Onsite', 'Offer']) {
    await expect(funnel.getByText(label, { exact: true })).toBeVisible();
  }
});
