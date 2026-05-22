import { type Route, expect, test } from '@playwright/test';

/**
 * Triage page E2E.
 *
 * All API calls go through Playwright's `route` interception — the
 * Vercel preview URL hits the Railway API by default, and the live
 * data drifts across runs. Mocking lets us assert exact UI behavior.
 *
 * The mock dataset is fixed at three postings with deterministic IDs
 * so tests can press J/K and predict the selection.
 */

const POSTINGS = [
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
    salary: null,
    source: { ats: 'greenhouse', url: 'https://example.test/jd/a' },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
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
    location_raw: 'SF',
    locations_normalized: ['SF'],
    remote_type: 'hybrid',
    salary: null,
    source: { ats: 'lever', url: 'https://example.test/jd/b' },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p-gamma',
    company: { id: 'c-3', name: 'Gamma Co', domain: null, description: null, tier: 3 },
    role: {
      title: 'Senior PM, Gamma',
      family: 'product_management',
      department: null,
      team: null,
      seniority: 'senior_pm',
    },
    location_raw: 'NYC',
    locations_normalized: ['NYC'],
    remote_type: 'onsite',
    salary: null,
    source: { ats: 'ashby', url: 'https://example.test/jd/c' },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
];

const CALIBRATION = {
  window: { since: new Date().toISOString(), until: new Date().toISOString() },
  surfaced: 10,
  interested: 4,
  interested_rate: 0.4,
  applied: 1,
  rejected_by_you: 2,
  top_rejected_role_families: [{ role_family: 'program_management', count: 3 }],
};

async function mockApi(page: import('@playwright/test').Page) {
  // The web app reads NEXT_PUBLIC_API_BASE_URL at build time. The preview
  // build embeds the Railway URL. Match both via a permissive glob so
  // tests work locally (localhost:8000) and in CI (Railway).
  await page.route('**/postings*', async (route: Route) => {
    const url = route.request().url();
    // Don't match POST /postings/{id}/state.
    if (route.request().method() !== 'GET') return route.continue();
    if (/\/postings\/[^/?]+(?:\?|$)/.test(url)) return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: POSTINGS.length,
        offset: 0,
        limit: 20,
        items: POSTINGS,
      }),
    });
  });
  await page.route('**/postings/*', async (route: Route) => {
    const method = route.request().method();
    if (method === 'POST') {
      // /state endpoint
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          current: 'interested',
          reason: null,
          snooze_until: null,
          current_at: new Date().toISOString(),
        }),
      });
      return;
    }
    // GET /postings/{id} — detail response
    const id = route.request().url().split('/').pop()?.split('?')[0];
    const item = POSTINGS.find((p) => p.id === id) ?? POSTINGS[0];
    // Per-posting summary so the JD-summary E2E can assert both shapes:
    // p-alpha keeps the legacy "no summary yet" behavior (raw JD visible
    // by default); p-beta returns a real summary so the toggle test can
    // collapse/expand it.
    const summaryByPosting: Record<string, string | null> = {
      'p-alpha': null,
      'p-beta': '**Scope**: Senior PM owns risk signals at Beta Co.',
      'p-gamma': null,
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...item,
        description_markdown: '## About the role\n\n- bullet',
        jd_summary_markdown: summaryByPosting[item.id] ?? null,
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
      body: JSON.stringify(CALIBRATION),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test('Triage page renders cards from the API', async ({ page }) => {
  await page.goto('/');
  // Each company name appears multiple times once the first card
  // auto-selects (card body + detail panel header + detail h3), so
  // query via the card's accessible-label aria-label.
  await expect(page.getByLabel(/Open detail for Alpha Co/)).toBeVisible();
  await expect(page.getByLabel(/Open detail for Beta Co/)).toBeVisible();
  await expect(page.getByLabel(/Open detail for Gamma Co/)).toBeVisible();
});

test('clicking a TIER chip updates URL search params', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'T1' }).click();
  await expect(page).toHaveURL(/tier=1/);
});

test('calibration KPIs render from the calibration endpoint', async ({ page }) => {
  await page.goto('/');
  // surfaced=10, interested=4 (40%)
  await expect(page.getByText('10', { exact: true })).toBeVisible();
  await expect(page.getByText('(40%)')).toBeVisible();
});

test('detail panel opens with markdown JD on card click', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel(/Open detail for Alpha Co/).click();
  await expect(page.getByRole('heading', { level: 2, name: /about the role/i })).toBeVisible();
});

test('saved-filter link navigates with the correct query params', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'T1+T2 · PM' }).click();
  await expect(page).toHaveURL(/tier=1.*tier=2.*role_family=product_management/);
});

test('clicking the Tune surfacing link navigates to /settings', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: /tune surfacing/i }).click();
  await expect(page).toHaveURL('/settings');
});

// ── PR #42: jd_summary_markdown in the detail panel ──────────────────────────

test('detail panel shows JD summary when jd_summary_markdown is present', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel(/Open detail for Beta Co/).click();
  // Summary heading + body.
  await expect(page.getByText(/job description \(summary\)/i)).toBeVisible();
  await expect(page.getByText(/Senior PM owns risk signals/i)).toBeVisible();
  // Toggle is offered, but the full JD body ("bullet") is NOT visible.
  await expect(page.getByRole('button', { name: /show full description/i })).toBeVisible();
  await expect(page.getByText('bullet', { exact: true })).toBeHidden();
});

test('toggle expands the full JD beneath the summary', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel(/Open detail for Beta Co/).click();
  await page.getByRole('button', { name: /show full description/i }).click();
  // After expansion the "Full description" subheading and the JD body
  // are both visible.
  await expect(page.getByRole('heading', { level: 5, name: /full description/i })).toBeVisible();
  await expect(page.getByText('bullet', { exact: true })).toBeVisible();
});

// ── PR #47: keyboard chord 2 → 1-9 opens the reason picker and commits ──────
// These tests exercise the full chord (not just the per-key unit handlers)
// because the audit caught a regression where the page-level `2` handler
// dispatched a toast but never opened any picker. Per-handler unit tests
// passed CI because they bypassed the chord.

test("keyboard '2' opens the inline reason picker for the focused card", async ({ page }) => {
  await page.goto('/');
  // p-alpha auto-selects on first render (index 0).
  await expect(page.getByLabel(/Open detail for Alpha Co/)).toBeVisible();
  // Confirm the picker is NOT yet open.
  await expect(page.getByText(/why not\?/i)).toBeHidden();
  // Fire the chord.
  await page.keyboard.press('2');
  // Picker mounts within ~1s with the full 9-chip vocabulary.
  await expect(page.getByText(/why not\?/i)).toBeVisible();
  await expect(page.getByRole('button', { name: /Wrong role 1/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Too senior 8/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Too junior 9/ })).toBeVisible();
  // Esc closes without committing.
  await page.keyboard.press('Escape');
  await expect(page.getByText(/why not\?/i)).toBeHidden();
});

test("keyboard '2' then '8' triggers the not_interested mutation flow", async ({ page }) => {
  // Diagnostic-only observer. Several earlier revisions of this test
  // (waitForRequest, page.route capture, optimistic-UI assertion)
  // failed in CI in ways that didn't reproduce locally. The
  // ``page.on('request')`` listener attaches BEFORE goto and is a
  // never-miss observer — every outgoing request, no glob matching,
  // no route precedence. Any POST anywhere proves mutate fired.
  const posts: string[] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST') posts.push(req.url());
  });

  await page.goto('/');
  await expect(page.getByLabel(/Open detail for Alpha Co/)).toBeVisible();

  // Fire the chord.
  await page.keyboard.press('2');
  await expect(page.getByText(/why not\?/i)).toBeVisible();
  await page.keyboard.press('8');
  // Picker closes — proves the picker's window listener fired and
  // onSelect propagated up at least as far as ``onToggleReason``.
  await expect(page.getByText(/why not\?/i)).toBeHidden();

  // At least one POST to a /state endpoint must have fired. If this
  // assertion fails despite the picker closing, the bug is that
  // ``handlePickReason`` somehow skipped its ``onAction`` call.
  await expect
    .poll(() => posts.find((u) => /\/postings\/[^/?]+\/state/.test(u)) ?? null, {
      timeout: 5_000,
    })
    .not.toBeNull();
});
