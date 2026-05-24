import { type Route, expect, test } from '@playwright/test';

import { mainContent, waitForDataReady } from './helpers';

/**
 * Contacts page E2E (PR #51).
 *
 * The shared ``mockApi`` helper covers /postings + /outcomes +
 * /companies + /stats/calibration but not /contacts. We intercept that
 * endpoint inline. Same Playwright route-mock pattern as other specs;
 * no real backend calls in CI.
 *
 * PII discipline: fixtures use obviously-fake names (``Test Person 1``,
 * ``Demo Recruiter``, ``Hiring Manager``). No real PII appears anywhere.
 */

const CONTACTS = [
  {
    id: 'c-alpha',
    first_name: 'Test',
    last_name: 'Alpha',
    preferred_first_name: null,
    email_primary: 'alpha@example.test',
    email_secondary: null,
    linkedin_url: 'https://linkedin.com/in/test-alpha',
    current_employer: 'ExampleCo',
    current_position: 'Senior PM, Platform',
    location_city: null,
    location_state: null,
    location_country: null,
    location_metro: null,
    source_type: 'tippie_alumni',
    target_company_id: null,
    archived_at: null,
    created_at: '2026-05-20T00:00:00Z',
  },
  {
    id: 'c-beta',
    first_name: 'Demo',
    last_name: 'Recruiter',
    preferred_first_name: null,
    email_primary: 'recruiter@example.test',
    email_secondary: null,
    linkedin_url: null,
    current_employer: 'StaffCo',
    current_position: 'Recruiter',
    location_city: null,
    location_state: null,
    location_country: null,
    location_metro: null,
    source_type: 'recruiter_inbound',
    target_company_id: null,
    archived_at: null,
    created_at: '2026-05-18T00:00:00Z',
  },
];

const ARCHIVED_CONTACT = {
  id: 'c-gamma',
  first_name: 'Test',
  last_name: 'Archived',
  preferred_first_name: null,
  email_primary: 'archived@example.test',
  email_secondary: null,
  linkedin_url: null,
  current_employer: null,
  current_position: null,
  location_city: null,
  location_state: null,
  location_country: null,
  location_metro: null,
  source_type: 'warm_intro',
  target_company_id: null,
  archived_at: '2026-05-01T00:00:00Z',
  created_at: '2026-04-30T00:00:00Z',
};

async function mockContactsApi(
  page: import('@playwright/test').Page,
  capturedUrls: string[] = [],
) {
  await page.route('**/contacts*', async (route: Route) => {
    // The glob ``**/contacts*`` matches both the API call and the
    // page-navigation URL (``/contacts``) on the same origin. Without
    // this guard, ``page.goto('/contacts')`` is intercepted by the mock
    // and the browser renders the raw JSON body instead of the React
    // app. Only intercept actual fetch/XHR API requests.
    const resourceType = route.request().resourceType();
    if (resourceType !== 'fetch' && resourceType !== 'xhr') {
      return route.continue();
    }
    const url = route.request().url();
    capturedUrls.push(url);
    // Honor include_archived + source_type filters in the mock so the
    // tests exercise the wire contract, not an in-memory shortcut.
    const u = new URL(url);
    const includeArchived = u.searchParams.get('include_archived') === 'true';
    const sources = u.searchParams.getAll('source_type');
    const search = u.searchParams.get('search')?.toLowerCase() ?? '';

    // Widen the union so the archived fixture's null-bearing optional
    // fields (linkedin_url, current_employer, current_position) don't
    // narrow against CONTACTS's string-typed fields.
    type ContactFixture = (typeof CONTACTS)[number] | typeof ARCHIVED_CONTACT;
    let items: ContactFixture[] = [...CONTACTS];
    if (includeArchived) items.push(ARCHIVED_CONTACT);
    if (sources.length) items = items.filter((c) => sources.includes(c.source_type));
    if (search) {
      items = items.filter((c) =>
        `${c.first_name} ${c.last_name}`.toLowerCase().includes(search),
      );
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: items.length,
        offset: 0,
        limit: 50,
        items,
      }),
    });
  });
}

test('renders contacts table from the API', async ({ page }) => {
  await mockContactsApi(page);
  await page.goto('/contacts');
  await waitForDataReady(page);

  // Asserting positive equality on the visible row content. Two
  // contact rows in the default view (archived hidden).
  await expect(mainContent(page).getByText('Test Alpha')).toBeVisible();
  await expect(mainContent(page).getByText('Demo Recruiter')).toBeVisible();
  // The source label ("Tippie alumni", "Recruiter inbound") surfaces
  // both as a filter chip ABOVE the table and as a source chip INSIDE
  // each row. Scope the row-content assertion to the <table> so the
  // chip collision doesn't trip strict-mode.
  const table = mainContent(page).getByRole('table');
  await expect(table.getByText('Tippie alumni')).toBeVisible();
  await expect(table.getByText('Recruiter inbound')).toBeVisible();
});

test('empty state renders when API returns no contacts', async ({ page }) => {
  await page.route('**/contacts*', async (route: Route) => {
    // See ``mockContactsApi`` for why this resourceType guard exists —
    // without it, the page navigation to ``/contacts`` is itself
    // intercepted and the browser renders the JSON body as text.
    const resourceType = route.request().resourceType();
    if (resourceType !== 'fetch' && resourceType !== 'xhr') {
      return route.continue();
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: 0, offset: 0, limit: 50, items: [] }),
    });
  });
  await page.goto('/contacts');
  await waitForDataReady(page);
  await expect(mainContent(page).getByTestId('contacts-empty')).toBeVisible();
  await expect(mainContent(page).getByText('No contacts yet.')).toBeVisible();
});

test('source filter chip narrows the list', async ({ page }) => {
  const capturedUrls: string[] = [];
  await mockContactsApi(page, capturedUrls);
  await page.goto('/contacts');
  await waitForDataReady(page);

  // Click the "Recruiter inbound" source filter chip. Aria-pressed is
  // wired so the chip can be located by accessible name.
  await page.getByRole('button', { name: 'Recruiter inbound' }).click();
  await waitForDataReady(page);

  // After the filter applies, Test Alpha (tippie_alumni) is gone;
  // Demo Recruiter (recruiter_inbound) remains.
  await expect(mainContent(page).getByText('Demo Recruiter')).toBeVisible();
  await expect(mainContent(page).getByText('Test Alpha')).toHaveCount(0);

  // The hook fired a fresh request with the source_type param.
  expect(capturedUrls.some((u) => u.includes('source_type=recruiter_inbound'))).toBe(true);
});

test('include archived toggle reveals archived contacts', async ({ page }) => {
  const capturedUrls: string[] = [];
  await mockContactsApi(page, capturedUrls);
  await page.goto('/contacts');
  await waitForDataReady(page);

  // Initially the archived contact is hidden.
  await expect(mainContent(page).getByText('Test Archived')).toHaveCount(0);

  // Flip the toggle.
  await page.getByLabel('Show archived').check();
  await waitForDataReady(page);

  await expect(mainContent(page).getByText('Test Archived')).toBeVisible();
  expect(capturedUrls.some((u) => u.includes('include_archived=true'))).toBe(true);
});

test('search input filters by name', async ({ page }) => {
  const capturedUrls: string[] = [];
  await mockContactsApi(page, capturedUrls);
  await page.goto('/contacts');
  await waitForDataReady(page);

  await page.getByLabel('SEARCH', { exact: true }).fill('alpha');
  // The hook debounces via react-query staleTime — wait for the
  // request to fire by checking the URL was captured.
  await expect.poll(() => capturedUrls.some((u) => u.includes('search=alpha'))).toBe(true);
  await waitForDataReady(page);

  await expect(mainContent(page).getByText('Test Alpha')).toBeVisible();
  await expect(mainContent(page).getByText('Demo Recruiter')).toHaveCount(0);
});
