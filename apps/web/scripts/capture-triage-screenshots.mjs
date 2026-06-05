// One-off visual capture for the score-forward restyle PR.
// Drives the REAL /triage page with mocked /api/be responses (same pattern as
// the e2e helpers) and screenshots the list + detail in light and dark.
//
//   node scripts/capture-triage-screenshots.mjs            (assumes :3100)
//   BASE_URL=http://localhost:3000 node scripts/...
//
// Uses the system Edge (Chromium download is blocked in this environment).
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const BASE = process.env.BASE_URL ?? 'http://localhost:3100';
const OUT = 'screenshots';
mkdirSync(OUT, { recursive: true });

const NOW = Date.now();
const ago = (mins) => new Date(NOW - mins * 60_000).toISOString();

// Varied fixtures: every score band (high/mid/low/null), every tier, a spread
// of role families, and a mix of remote types / salaries / sources.
const ITEMS = [
  {
    id: 'p1', score: 92,
    company: { id: 'c1', name: 'Stripe', domain: 'stripe.com', description: 'Payments infrastructure for the internet.', tier: 1 },
    role: { title: 'Senior Product Manager, Payments', family: 'product_management', department: 'Payments', team: 'Auth', seniority: 'senior_pm' },
    location_raw: 'San Francisco, CA', locations_normalized: ['San Francisco, CA'], remote_type: 'hybrid',
    salary: { min: 220000, max: 280000, currency: 'USD', period: 'annual' },
    source: { ats: 'greenhouse', url: 'https://example.test/1' }, first_seen_at: ago(35),
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p2', score: 88,
    company: { id: 'c2', name: 'Linear', domain: 'linear.app', description: 'The issue tracker built for modern teams.', tier: 1 },
    role: { title: 'Group Product Manager', family: 'product_management', department: null, team: null, seniority: 'gpm' },
    location_raw: 'Remote — US', locations_normalized: ['Remote'], remote_type: 'remote',
    salary: { min: 200000, max: 250000, currency: 'USD', period: 'annual' },
    source: { ats: 'ashby', url: 'https://example.test/2' }, first_seen_at: ago(95),
    state: { current: 'interested', reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p3', score: 71,
    company: { id: 'c3', name: 'John Hancock / Manulife US', domain: 'johnhancock.com', description: 'Insurance and wealth management.', tier: 2 },
    role: { title: 'Global Digital Product Manager', family: 'product_management', department: null, team: null, seniority: 'pm' },
    location_raw: 'Boston, Massachusetts; Toronto, Ontario', locations_normalized: ['Boston', 'Toronto'], remote_type: 'onsite',
    salary: { min: 140000, max: 175000, currency: 'USD', period: 'annual' },
    source: { ats: 'workday', url: 'https://example.test/3' }, first_seen_at: ago(180),
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p4', score: 63,
    company: { id: 'c4', name: 'Notion', domain: 'notion.so', description: 'The connected workspace.', tier: 2 },
    role: { title: 'Technical Product Owner, Platform', family: 'product_owner', department: 'Platform', team: 'API', seniority: 'po' },
    location_raw: 'New York, NY', locations_normalized: ['New York, NY'], remote_type: 'hybrid',
    salary: { min: 160000, max: 200000, currency: 'USD', period: 'annual' },
    source: { ats: 'lever', url: 'https://example.test/4' }, first_seen_at: ago(300),
    state: { current: 'applied', reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p5', score: 48,
    company: { id: 'c5', name: 'Acme Logistics', domain: 'acme.example', description: 'Freight and supply chain.', tier: 3 },
    role: { title: 'Program Manager, Operations', family: 'program_management', department: null, team: null, seniority: 'pgm' },
    location_raw: 'Austin, TX', locations_normalized: ['Austin, TX'], remote_type: 'onsite',
    salary: { min: 120000, max: 150000, currency: 'USD', period: 'annual' },
    source: { ats: 'icims', url: 'https://example.test/5' }, first_seen_at: ago(700),
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p6', score: 31,
    company: { id: 'c6', name: 'Globex Marketing', domain: 'globex.example', description: 'Brand and growth agency.', tier: 4 },
    role: { title: 'Product Marketing Manager', family: 'product_marketing', department: null, team: null, seniority: 'pmm' },
    location_raw: 'Chicago, IL', locations_normalized: ['Chicago, IL'], remote_type: 'remote',
    salary: { min: 95000, max: 120000, currency: 'USD', period: 'annual' },
    source: { ats: 'greenhouse', url: 'https://example.test/6' }, first_seen_at: ago(1500),
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p7', score: 22,
    company: { id: 'c7', name: 'Initech', domain: 'initech.example', description: 'Enterprise middleware.', tier: 4 },
    role: { title: 'Associate Product Analyst', family: 'other', department: null, team: null, seniority: 'other' },
    location_raw: 'Dallas, TX', locations_normalized: ['Dallas, TX'], remote_type: 'onsite',
    salary: null,
    source: { ats: 'workday', url: 'https://example.test/7' }, first_seen_at: ago(2600),
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
  {
    id: 'p8', score: null,
    company: { id: 'c8', name: 'Figma', domain: 'figma.com', description: 'Collaborative design platform.', tier: 1 },
    role: { title: 'Principal Product Manager, Editor', family: 'product_management', department: 'Editor', team: 'Canvas', seniority: 'principal' },
    location_raw: 'Remote — US', locations_normalized: ['Remote'], remote_type: 'remote',
    salary: { min: 240000, max: 320000, currency: 'USD', period: 'annual' },
    source: { ats: 'ashby', url: 'https://example.test/8' }, first_seen_at: ago(20),
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  },
];

const LIST = { total: ITEMS.length, offset: 0, limit: 20, items: ITEMS };

function detailFor(item) {
  return {
    ...item,
    score: item.score,
    division:
      item.role.department || item.role.team
        ? { department: item.role.department ?? 'Product', team: item.role.team, description: 'Owns the end-to-end roadmap for this surface.' }
        : null,
    posted_at: item.first_seen_at, last_seen_at: item.first_seen_at, closed_at: null,
    state: { ...item.state, resolved_status: item.state.current === 'applied' ? 'applied' : null, gmail_rejection_hint: false },
    state_history: [], resume: null,
    description_markdown: '## About the role\n\nWe are looking for a product leader to own a high-impact surface.\n\n- Drive strategy and execution\n- Partner with design and engineering\n- Define and track success metrics',
    jd_summary_markdown: item.id === 'p1'
      ? '**Scope**: Own the payments authorization roadmap end to end.\n\n**Comp**: $220k–$280k + equity.\n\n**Why it fits**: Strong match on fintech PM experience and 0→1 platform work.'
      : null,
  };
}

const empty = { total: 0, offset: 0, limit: 2000, items: [] };

async function mock(page) {
  await page.route('**/postings/*', async (route) => {
    const url = route.request().url();
    const m = url.match(/\/postings\/([^/?]+)/);
    const id = m?.[1];
    const item = ITEMS.find((it) => it.id === id);
    if (!item) return route.continue();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detailFor(item)) });
  });
  await page.route('**/postings*', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const url = route.request().url();
    if (/\/postings\/[^/?]+(?:\?|$)/.test(url)) return route.continue();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(LIST) });
  });
  for (const ep of ['**/outcomes*', '**/companies*', '**/stats/calibration*', '**/resume*']) {
    await page.route(ep, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(empty) }));
  }
}

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(900);
}

async function run() {
  const browser = await chromium.launch({ channel: 'msedge' });
  for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: theme, deviceScaleFactor: 2 });
    // next-themes (attribute="class", default storageKey "theme") reads this on
    // mount; setting it before any document loads makes the theme stick.
    await ctx.addInitScript((t) => {
      try { window.localStorage.setItem('theme', t); } catch {}
    }, theme);
    const page = await ctx.newPage();
    await mock(page);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
    await settle(page);

    // EXPANDED-ON-SELECT: the page auto-selects the first card, so the detail
    // panel is already at its wide (selected) width with content. Capture the
    // zone separation + wide readable panel.
    await page.screenshot({ path: `${OUT}/triage-expanded-${theme}.png`, fullPage: false });

    // NEUTRAL/COLLAPSED: deselect (close) → the panel animates back to its
    // narrow resting width and the list reclaims the space. Wait past the
    // ~300ms width transition before shooting.
    await page.getByLabel('Close detail panel').click().catch(() => {});
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${OUT}/triage-neutral-${theme}.png`, fullPage: false });

    await ctx.close();
    console.log(`captured ${theme}`);
  }
  await browser.close();
}

run().then(() => { console.log('done'); process.exit(0); }).catch((e) => { console.error(e); process.exit(1); });
