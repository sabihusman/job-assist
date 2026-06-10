/**
 * Normalize a company name to a stable lookup key (feat/company-app-awareness).
 *
 * MUST stay in lockstep with the backend
 * ``apps/api/src/job_assist/services/company_name_match.py`` ``normalize_company_name``
 * — the per-company signal map is keyed by this normalized name on the server,
 * and the triage badge re-normalizes the posting's ``company.name`` to look it
 * up. If the two diverge, badges silently stop matching.
 *
 *   "Stripe, Inc." / "STRIPE" / "stripe" → "stripe"
 *   "greenhouse.io" → null   (ATS vendor, not a company)
 */

// Legal-entity suffix tokens dropped so "Stripe, Inc." and "Stripe" collapse.
// Conservative: "technologies" / "labs" / "group" are NOT dropped (they
// distinguish real, different companies).
const SUFFIX_TOKENS = new Set([
  'inc',
  'incorporated',
  'llc',
  'ltd',
  'limited',
  'corp',
  'corporation',
  'co',
  'company',
  'gmbh',
  'plc',
  'sa',
  'ag',
  'nv',
  'bv',
  'llp',
  'lp',
  'pty',
]);

// ATS / job-board vendor tokens — an extracted "company" that is really one of
// these is not a real company.
const VENDOR_TOKENS = new Set([
  'greenhouse',
  'greenhouseio',
  'lever',
  'leverco',
  'ashby',
  'ashbyhq',
  'workday',
  'myworkday',
  'myworkdayjobs',
  'icims',
  'jobvite',
  'smartrecruiters',
  'workable',
  'breezy',
  'breezyhr',
  'bamboohr',
  'noreply',
  'donotreply',
]);

// TLD-ish noise tokens dropped so a bare ATS domain ("greenhouse.io") reduces to
// its vendor token and is then rejected as a non-company.
const TLD_TOKENS = new Set(['com', 'io', 'net', 'org']);

function tokens(name: string): string[] {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(' ')
    .filter((t) => t && !SUFFIX_TOKENS.has(t) && !TLD_TOKENS.has(t));
}

/** Normalized key, or null when the name is empty/all-suffix/a vendor token. */
export function normalizeCompanyName(name: string | null | undefined): string | null {
  if (!name) return null;
  const toks = tokens(name);
  if (toks.length === 0) return null;
  if (toks.every((t) => VENDOR_TOKENS.has(t))) return null;
  return toks.join(' ');
}
