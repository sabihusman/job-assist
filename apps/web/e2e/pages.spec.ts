import { expect, test, type Route } from '@playwright/test';

/**
 * E2E coverage for the four new pages in PR #32c. Each spec mocks
 * the relevant endpoints at the network layer so runs are
 * deterministic regardless of Railway data drift.
 */

const NOW = new Date();
const recentIso = (daysAgo: number) =>
  new Date(NOW.getTime() - daysAgo * 86_400_000).toISOString();

const APPLIED_POSTINGS = [
  {
    id: 'p-alpha',
    company: { id: 'c-1', name: 'Alpha Co', domain: null, description: null, tier: 1 },
    role: { title: 'Senior PM, Alpha', family: 'product_management', department: null, team: null, seniority: 'senior_pm' },
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
    role: { title: 'Senior PM, Beta', family: 'product_management', department: null, team: null, seniority: 'senior_pm' },
    location_raw: null,
    locations_normalized: [],
    remote_type: 'hybrid',
    salary: null,
    source: { ats: 'lever', url: null },
    first_seen_at: recentIso(3),
    score: null,
    state: { current: 'applied', reason: null, snooze_until: null, current_at: recentIso(3) },
  },
];

const OUTCOMES = [
  {
    id: 'o-1',
    posting_id: 'p-alpha',
    received_at: recentIso(2),
    stage: 'recruiter_screen_invite',
    confidence: 0.9,
  },
];

const COMPANIES = [
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
];

const CALIBRATION = {
  window: { since: recentIso(7), until: NOW.toISOString() },
  surfaced: 42,
  interested: 13,
  interested_rate: 0.31,
  applied: 2,
  rejected_by_you: 5,
  top_rejected_role_families: [],
};

async function mockApi(page: import('@playwright/test').Page) {
  await page.route('**/postings*', async (route: Route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const url = route.request().url();
    if (/\/postings\/[^/?]+(?:\?|$)/.test(url)) return route.continue();
    // Both Triage and Applied hit /postings; just always return the
    // applied dataset — the Triage page's own E2E spec already covers
    // the broader filter behavior.
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: APPLIED_POSTINGS.length,
        offset: 0,
        limit: 500,
        items: APPLIED_POSTINGS,
      }),
    });
  });
  await page.route('**/outcomes*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: OUTCOMES.length,
        offset: 0,
        limit: 2000,
        items: OUTCOMES,
      }),
    });
  });
  await page.route('**/companies*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: COMPANIES.length,
        offset: 0,
        limit: 100,
        items: COMPANIES,
      }),
    });
  });
  await page.route('**/stats/calibration*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CALIBRATION),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

// ── Applied ─────────────────────────────────────────────────────────────

test('Applied page loads and renders one row per applied posting', async ({ page }) => {
  await page.goto('/applied');
  await expect(page.getByText('Alpha Co')).toBeVisible();
  await expect(page.getByText('Beta Co')).toBeVisible();
});

test('Applied row expand reveals TIMELINE label', async ({ page }) => {
  await page.goto('/applied');
  await page.getByRole('button', { expanded: false }).first().click();
  await expect(page.getByText(/timeline/i).first()).toBeVisible();
});

test('Applied sort=tier reorders the URL', async ({ page }) => {
  await page.goto('/applied');
  // Wait for the row(s) to settle so the sort strip is unambiguously
  // present and not racing the data fetch.
  await expect(page.getByText('Alpha Co')).toBeVisible();
  // Scope to the "sort:" group — multiple buttons may exist on the
  // page (e.g. row toggles) and exact-name selection could be flaky.
  const sortGroup = page.locator('text=sort:').locator('xpath=..');
  await sortGroup.getByRole('button', { name: 'tier' }).click();
  await expect(page).toHaveURL(/sort=tier/);
});

// ── Pipeline ────────────────────────────────────────────────────────────

test('Pipeline page renders 8 stage columns in order', async ({ page }) => {
  await page.goto('/pipeline');
  const expected = ['APPLIED', 'RECRUITER', 'PHONE', 'VIDEO', 'ONSITE', 'OFFER', 'REJECTED', 'GHOSTED'];
  for (const label of expected) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
});

test('Pipeline buckets the alpha posting into RECRUITER (latest outcome)', async ({ page }) => {
  await page.goto('/pipeline');
  const recruiter = page.getByRole('region', { name: /recruiter screen/i });
  await expect(recruiter.getByText('Alpha Co')).toBeVisible();
});

// ── Companies ───────────────────────────────────────────────────────────

test('Companies table shows 6 column headers and company rows', async ({ page }) => {
  await page.goto('/companies');
  // Wait for data to land before asserting on the table — otherwise the
  // <table> element doesn't exist yet (the page renders a skeleton).
  await expect(page.getByText('Alpha Co')).toBeVisible();
  await expect(page.getByRole('columnheader', { name: /^Name$/i })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: /^Outcomes$/i })).toBeVisible();
  // Notes column is stripped — must NOT be present.
  expect(await page.getByRole('columnheader', { name: /^Notes$/i }).count()).toBe(0);
});

test('Companies subtitle reports target count', async ({ page }) => {
  await page.goto('/companies');
  // The subtitle updates from "Target list" → "2 target companies"
  // once the /companies fetch settles, so wait for the data first.
  await expect(page.getByText('Alpha Co')).toBeVisible();
  await expect(page.getByText(/2 target companies/)).toBeVisible();
});

// ── Stats ───────────────────────────────────────────────────────────────

test('Stats page renders the KPI grid and funnel section', async ({ page }) => {
  await page.goto('/stats');
  await expect(page.getByText(/postings ingested \(7d\)/i)).toBeVisible();
  await expect(page.getByText(/outcome funnel/i)).toBeVisible();
});

test('Stats funnel shows all 6 stage labels', async ({ page }) => {
  await page.goto('/stats');
  // The Sidebar nav has an "Applied" link and "Onsite" appears
  // nowhere else, but `Applied` would resolve to 2 elements. Scope
  // queries to the funnel section's <ol>.
  const funnel = page
    .locator('section')
    .filter({ has: page.getByText(/outcome funnel/i) })
    .getByRole('list');
  for (const label of [
    'Applied',
    'Recruiter screen',
    'Phone interview',
    'Video interview',
    'Onsite',
    'Offer',
  ]) {
    await expect(funnel.getByText(label, { exact: true })).toBeVisible();
  }
});
