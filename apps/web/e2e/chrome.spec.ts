import { expect, test } from '@playwright/test';

/**
 * E2E coverage for the chrome shipped in PR #32a.
 *
 * Five scenarios:
 *   1. Sidebar nav clicks → URL changes through all six routes
 *   2. Command palette ⌘K → type → Enter navigates
 *   3. Theme toggle persists across navigation
 *   4. Every route renders chrome + placeholder card
 *   5. /outreach returns 404 (Outreach is fully stripped from v1)
 */

const ROUTES = [
  { href: '/', title: 'Triage' },
  { href: '/applied', title: 'Applied' },
  { href: '/pipeline', title: 'Pipeline' },
  { href: '/companies', title: 'Companies' },
  { href: '/stats', title: 'Stats' },
  { href: '/settings', title: 'Settings' },
] as const;

test('sidebar nav cycles through all six routes', async ({ page }) => {
  await page.goto('/');
  for (const { href, title } of ROUTES) {
    await page.getByRole('link', { name: title, exact: true }).click();
    await expect(page).toHaveURL(href);
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  }
});

test('command palette navigates via keyboard', async ({ page }) => {
  await page.goto('/');
  // Open via ⌘K / Ctrl+K — the listener handles both.
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+k' : 'Control+k');
  await expect(page.getByPlaceholder(/search commands/i)).toBeVisible();
  await page.getByPlaceholder(/search commands/i).fill('applied');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL('/applied');
});

// PR #32a's `placeholder card renders on every route` test was reduced
// in #32c to just `/settings`, since the other five routes became real
// pages. PR #32d makes `/settings` real too, so the test is obsolete.
// Real-page coverage lives in pages.spec.ts (Applied/Pipeline/Companies/
// Stats) and settings.spec.ts (Settings).

test('theme toggle persists across navigation', async ({ page }) => {
  await page.goto('/settings');
  await page.getByRole('button', { name: /^dark$/i }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await page.goto('/pipeline');
  await expect(page.locator('html')).toHaveClass(/dark/);
});

test('/outreach returns 404', async ({ page }) => {
  const response = await page.goto('/outreach');
  expect(response?.status()).toBe(404);
});
