import { expect, test, type Route } from '@playwright/test';

import { mainContent, mockApi } from './helpers';

/**
 * Settings page E2E (PR #32d).
 *
 * Conventions from PR #32c carry forward — use `mainContent(page)` to
 * scope queries to the AppShell's <main> region, and use `mockApi`
 * for the four common endpoints. Settings additionally needs
 * /operator/profile mocked, which doesn't fit the helper's contract.
 *
 * Cross-page navigation in these specs deliberately goes to /pipeline
 * (not /companies) because the Companies E2E has a known Vercel-
 * preview-specific failure mode documented in PR #32c.
 */

const DEFAULT_PROFILE = {
  id: 1,
  looking_for_text: 'Senior PM roles · staff IC welcome',
  role_keywords: ['product manager', 'senior pm'],
  geo_whitelist: ['Remote US', 'NYC'],
  salary_floor_usd: 85000,
  applicant_cap: 150,
  staffing_firm_blocklist: ['Robert Half', 'Aerotek'],
  created_at: '2026-04-01T00:00:00Z',
  updated_at: '2026-04-01T00:00:00Z',
};

const DISCOVER_ATS_RESPONSE = {
  committed: false,
  matched_count: 0,
  unmatched_count: 0,
  matched: [],
  unmatched: [],
};

async function mockSettingsApi(page: import('@playwright/test').Page) {
  await mockApi(page, {});
  await page.route(/\/operator\/profile/, async (route: Route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(DEFAULT_PROFILE),
      });
    } else if (route.request().method() === 'PUT') {
      // Echo back the request body merged onto the default profile —
      // simulates a successful partial update.
      const body = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...DEFAULT_PROFILE, ...body }),
      });
    } else {
      await route.continue();
    }
  });
  await page.route(/\/admin\/discover-ats\/run/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(DISCOVER_ATS_RESPONSE),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockSettingsApi(page);
});

test('Settings page loads and renders all 5 section headings', async ({ page }) => {
  await page.goto('/settings');
  const content = mainContent(page);
  await expect(content.getByRole('heading', { name: 'Appearance' })).toBeVisible();
  // Wait for profile data to land before checking later sections.
  await expect(content.getByRole('heading', { name: 'Profile' })).toBeVisible({ timeout: 10_000 });
  await expect(content.getByRole('heading', { name: 'Hard rule thresholds' })).toBeVisible();
  await expect(content.getByRole('heading', { name: 'API keys' })).toBeVisible();
  await expect(content.getByRole('heading', { name: 'Manual job triggers' })).toBeVisible();
});

test('Settings theme toggle persists across navigation to /pipeline', async ({ page }) => {
  // Cross-page nav uses /pipeline (not /companies — that has a known
  // Vercel-preview failure mode tracked separately).
  await page.goto('/settings');
  await page.getByRole('button', { name: /^dark$/i }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await page.goto('/pipeline');
  await expect(page.locator('html')).toHaveClass(/dark/);
});

test('API keys section renders all 5 env-var rows', async ({ page }) => {
  await page.goto('/settings');
  const content = mainContent(page);
  for (const name of [
    'DATABASE_URL',
    'GEMINI_API_KEY',
    'ANTHROPIC_API_KEY',
    'GMAIL_CREDENTIALS_JSON',
    'GMAIL_REFRESH_TOKEN',
  ]) {
    await expect(content.getByText(name)).toBeVisible();
  }
});

test('Manual job: discover-ats run shows RESPONSE panel', async ({ page }) => {
  await page.goto('/settings');
  const content = mainContent(page);
  // Wait for the manual jobs section to render.
  const runRow = content
    .locator('div')
    .filter({ hasText: 'Run discover-ats' })
    .first();
  await runRow.getByRole('button', { name: /run/i }).click();
  // RESPONSE panel renders inline with the JSON body.
  await expect(content.getByText(/^Response$/i)).toBeVisible({ timeout: 10_000 });
});

test('Settings footer renders only on /settings', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByRole('contentinfo')).toBeVisible();
  await page.goto('/pipeline');
  // No contentinfo footer on Pipeline (or any non-Settings page).
  expect(await page.getByRole('contentinfo').count()).toBe(0);
});

test('Profile save toasts on success', async ({ page }) => {
  await page.goto('/settings');
  const content = mainContent(page);
  // Wait for the form to be hydrated with the profile data.
  await expect(content.getByText('product manager')).toBeVisible({ timeout: 10_000 });
  // Add a new role keyword. Playwright's Locator API uses
  // `getByLabel`, not Testing Library's `getByLabelText`.
  const tagInput = content.getByLabel('Add role keyword');
  await tagInput.fill('staff pm');
  await tagInput.press('Enter');
  await content.getByRole('button', { name: /save profile/i }).click();
  // Sonner toast appears bottom-right with the success copy.
  await expect(page.getByText(/profile saved/i)).toBeVisible({ timeout: 5000 });
});
