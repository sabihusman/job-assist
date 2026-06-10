import { expect, test } from '@playwright/test';

import { mainContent, mockApi, waitForDataReady } from './helpers';

/**
 * /rejected page E2E (PR #50; feat/rejected-unified).
 *
 * Wire-level contract: `/rejected/page.tsx` now UNIFIES the manual rejected
 * funnel (`GET /postings?state=rejected`) with Gmail-detected rejections
 * (`GET /outcomes`) via `unifyApplied`, then filters to the rejected stage —
 * mirroring the Applied tab (#163). A manual rejected posting must carry
 * `state.resolved_status='rejected'` (the API always sends it for state=rejected)
 * so the unifier keeps it. `/outcomes` defaults to empty in `mockApi`, so this
 * spec exercises the manual side.
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
    // state=rejected ⇒ resolved_status='rejected' (manual application_state).
    resolved_status: 'rejected',
  },
};

test('renders rejected rows from the API', async ({ page }) => {
  await mockApi(page, {
    postings: { total: 1, offset: 0, limit: 500, items: [REJECTED_ITEM] },
  });
  await page.goto('/rejected');
  await waitForDataReady(page);

  // AppShell renders the page title in <Banner>, OUTSIDE <main aria-
  // label="Page content">. Assert the row content instead — that proves
  // the page rendered and avoids colliding with the "Rejected" sidebar
  // link (added in PR #50).
  await expect(mainContent(page).getByText('RejCo')).toBeVisible();
  await expect(mainContent(page).getByText('Lead PM, Growth')).toBeVisible();
});

test('renders empty state when no rejected postings exist', async ({ page }) => {
  await mockApi(page, { postings: { total: 0, offset: 0, limit: 500, items: [] } });
  await page.goto('/rejected');
  await waitForDataReady(page);

  await expect(mainContent(page).getByTestId('rejected-empty')).toBeVisible();
  await expect(mainContent(page).getByText('No rejections yet.')).toBeVisible();
});
