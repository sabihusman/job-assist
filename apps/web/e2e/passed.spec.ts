import { expect, test } from '@playwright/test';

import { mainContent, mockApi, waitForDataReady } from './helpers';

/**
 * /passed page E2E (PR #50).
 *
 * Wire-level contract: `/passed/page.tsx` calls `usePassedPostings()`
 * which hits `GET /postings?state=not_interested`. The shared `mockApi`
 * helper intercepts every `**\/postings*` GET regardless of query, so
 * the fixture just supplies one shape and both data + empty paths
 * exercise the same network surface.
 */

const PASSED_ITEM = {
  id: 'p-passed-alpha',
  company: { id: 'c-1', name: 'PassedCo', domain: null, description: null, tier: 1 },
  role: {
    title: 'Senior PM, Platform',
    family: 'product_management',
    department: null,
    team: null,
    seniority: 'senior_pm',
  },
  location_raw: 'Remote',
  locations_normalized: ['Remote'],
  remote_type: 'remote',
  salary: null,
  source: { ats: 'greenhouse', url: 'https://example.test/jd/a' },
  first_seen_at: new Date().toISOString(),
  score: null,
  state: {
    current: 'not_interested',
    reason: 'too_senior',
    snooze_until: null,
    current_at: new Date().toISOString(),
  },
};

test('renders passed rows from the API', async ({ page }) => {
  await mockApi(page, {
    postings: { total: 1, offset: 0, limit: 500, items: [PASSED_ITEM] },
  });
  await page.goto('/passed');
  await waitForDataReady(page);

  // Page title + the one row land in <main>.
  await expect(mainContent(page).getByText('Passed', { exact: true })).toBeVisible();
  await expect(mainContent(page).getByText('PassedCo')).toBeVisible();
  await expect(mainContent(page).getByText('Senior PM, Platform')).toBeVisible();
  // Reason chip — label comes from REASON_CHOICES vocabulary.
  await expect(mainContent(page).getByLabel('Reason: Too senior')).toBeVisible();
});

test('renders empty state when no passed postings exist', async ({ page }) => {
  await mockApi(page, { postings: { total: 0, offset: 0, limit: 500, items: [] } });
  await page.goto('/passed');
  await waitForDataReady(page);

  await expect(mainContent(page).getByTestId('passed-empty')).toBeVisible();
  await expect(mainContent(page).getByText('No passed postings yet.')).toBeVisible();
});
