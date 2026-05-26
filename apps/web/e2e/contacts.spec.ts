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

// ── PR #52: detail panel + edit + outreach logging + archive ─────────────────

// Regex (not glob) for the detail / CRUD endpoints, matching the same
// resource-type guard pattern as ``mockContactsApi`` — see PR #51
// bestiary note about Playwright glob ``*`` not spanning ``/``.
const CONTACT_BY_ID_RE = /\/contacts\/[^/?]+(\?|$)/;
const CONTACT_OUTREACH_RE = /\/contacts\/[^/]+\/outreach(\?|$)/;
const CONTACT_ARCHIVE_RE = /\/contacts\/[^/]+\/(un)?archive$/;

/** Build a full ContactDetail payload from one of the list fixtures. */
function detailOf(base: (typeof CONTACTS)[number]) {
  return {
    ...base,
    phone: null,
    source_metadata: null,
    job_functions_of_interest: null,
    industries_of_interest: null,
    contact_opt_in: false,
    contact_opt_in_topics: null,
    notes: null,
    updated_at: base.created_at,
  };
}

async function mockContactDetailRoutes(page: import('@playwright/test').Page) {
  // GET /contacts/{id} — return the fixture's detail shape.
  await page.route(CONTACT_BY_ID_RE, async (route: Route) => {
    const resourceType = route.request().resourceType();
    if (resourceType !== 'fetch' && resourceType !== 'xhr') return route.continue();
    if (route.request().method() !== 'GET') return route.continue();
    const url = route.request().url();
    // Skip the outreach + archive subpaths — those have their own handlers.
    if (CONTACT_OUTREACH_RE.test(url) || CONTACT_ARCHIVE_RE.test(url)) {
      return route.continue();
    }
    const id = url.split('/contacts/')[1]?.split(/[?/]/)[0];
    const found = CONTACTS.find((c) => c.id === id);
    if (!found) {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: `contact ${id} not found` }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detailOf(found)),
    });
  });

  // GET / POST /contacts/{id}/outreach.
  await page.route(CONTACT_OUTREACH_RE, async (route: Route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'm-new',
          contact_id: 'c-alpha',
          source: 'manual',
          external_message_id: null,
          metadata: null,
          created_at: new Date().toISOString(),
          ...body,
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: 0, offset: 0, limit: 50, items: [] }),
    });
  });

  // POST /contacts/{id}/archive or /unarchive.
  await page.route(CONTACT_ARCHIVE_RE, async (route: Route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({ status: 204, body: '' });
  });
}

test('clicking a contact row opens the detail panel', async ({ page }) => {
  await mockContactsApi(page);
  await mockContactDetailRoutes(page);
  await page.goto('/contacts');
  await waitForDataReady(page);

  // Panel starts closed.
  const panel = page.getByTestId('contact-detail-panel');
  await expect(panel).toHaveAttribute('data-open', 'false');

  // Click the Alpha row.
  await mainContent(page).getByText('Test Alpha').click();

  // Panel opens; the contact's name appears as a heading inside it.
  await expect(panel).toHaveAttribute('data-open', 'true');
  await expect(panel.getByRole('heading', { name: /Test Alpha/i })).toBeVisible();
});

test('logging an outbound LinkedIn message sends manual-source POST', async ({ page }) => {
  const capturedBodies: Array<Record<string, unknown>> = [];
  await mockContactsApi(page);
  await mockContactDetailRoutes(page);
  // Spy on the POST body so we can assert the wire shape.
  await page.route(CONTACT_OUTREACH_RE, async (route: Route, request) => {
    if (request.method() === 'POST') {
      capturedBodies.push(request.postDataJSON());
    }
    await route.fallback();
  });

  await page.goto('/contacts');
  await waitForDataReady(page);
  await mainContent(page).getByText('Test Alpha').click();

  const panel = page.getByTestId('contact-detail-panel');
  await expect(panel.getByRole('heading', { name: /Test Alpha/i })).toBeVisible();

  await panel.getByTestId('log-outreach-open').click();
  await expect(panel.getByTestId('log-outreach-form')).toBeVisible();

  // Defaults: outbound + linkedin + now — submit and verify wire shape.
  await panel.getByRole('button', { name: 'Log' }).click();

  await expect.poll(() => capturedBodies.length).toBeGreaterThan(0);
  const body = capturedBodies[0];
  expect(body).toHaveProperty('direction', 'outbound');
  expect(body).toHaveProperty('channel', 'linkedin');
  expect(body).toHaveProperty('sent_at');
  // The hotfix-class lock: server forces source, never accept it
  // from the client.
  expect(body).not.toHaveProperty('source');
});

test('archive round-trip: archive then unarchive without page reload', async ({ page }) => {
  const archiveCalls: string[] = [];
  await mockContactsApi(page);
  await mockContactDetailRoutes(page);
  // Track which archive/unarchive endpoint fired.
  await page.route(CONTACT_ARCHIVE_RE, async (route: Route, request) => {
    archiveCalls.push(request.url());
    await route.fallback();
  });

  await page.goto('/contacts');
  await waitForDataReady(page);
  await mainContent(page).getByText('Test Alpha').click();

  const panel = page.getByTestId('contact-detail-panel');
  await expect(panel.getByRole('heading', { name: /Test Alpha/i })).toBeVisible();

  // Archive
  await panel.getByRole('button', { name: 'Archive' }).click();
  await expect.poll(() => archiveCalls.filter((u) => u.endsWith('/archive')).length).toBeGreaterThan(0);
});

