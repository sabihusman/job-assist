import { expect, test } from '@playwright/test';

import { mainContent, mockApi, waitForDataReady } from './helpers';

/**
 * /rejected page E2E (PR #50).
 *
 * Wire-level contract: `/rejected/page.tsx` calls `useRejectedPostings()`
 * which hits `GET /postings?state=rejected`. The backend EXISTS predicate
 * against outcome_event is opaque to the frontend — the test treats it
 * as a black box and asserts the page renders whatever the API returns.
 */

const REJECTED_ITEM = {
  id: 'p-rejected-alpha',
  company: { id: 'c-1', name: 'RejCo', domain: null, description: null, tier: 2 },
  role: {
    title: 'Lead PM, Growth',
    family: 'product_management',
    department: null,
    team: null,
    seniority: 'lead_pm',
  },
  location_raw: 'NYC',
  locations_normalized: ['NYC'],
  remote_type: 'onsite',
  salary: null,
  source: { ats: 'ashby', url: 'https://example.test/jd/r' },
  first_seen_at: new Date().toISOString(),
  score: null,
  state: {
    current: 'applied',
    reason: null,
    snooze_until: null,
    current_at: new Date().toISOString(),
  },
};

test('renders rejected rows from the API', async ({ page }) => {
  await mockApi(page, {
    postings: { total: 1, offset: 0, limit: 500, items: [REJECTED_ITEM] },
  });
  await page.goto('/rejected');
  await waitForDataReady(page);

  await expect(mainContent(page).getByText('Rejected', { exact: true })).toBeVisible();
  await expect(mainContent(page).getByText('RejCo')).toBeVisible();
  await expect(mainContent(page).getByText('Lead PM, Growth')).toBeVisible();
});

test('renders empty state when no rejected postings exist', async ({ page }) => {
  await mockApi(page, { postings: { total: 0, offset: 0, limit: 500, items: [] } });
  await page.goto('/rejected');
  await waitForDataReady(page);

  await expect(mainContent(page).getByTestId('rejected-empty')).toBeVisible();
  await expect(mainContent(page).getByText('No rejected postings yet.')).toBeVisible();
});
