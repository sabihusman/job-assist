/**
 * Derive a company name from an ATS confirmation/rejection email subject.
 *
 * The data check (feat/pipeline-outcome-cards) confirmed the dominant prod
 * pattern is "Thank you for applying to <Company>" and close variants, and
 * that `from_domain` is the ATS vendor (greenhouse.io / ashbyhq.com) — useless
 * as a company label. `outcome_event.target_company_id` is set for only ~29 of
 * ~197 lifecycle rows, so for the unlinked majority the subject is the only
 * place a real company name lives.
 *
 * Returns the extracted company, or `null` when the subject is generic
 * ("Update on Your Application") so the caller can fall back.
 */

// "...applying to/at/with <X>" or "...application to/with <X>". Anchored on the
// apply verb (not the start), case-insensitive; captures the trailing remainder
// which `cleanCompany` then trims down. "for" is deliberately excluded — "applying
// for the <role>" is about the role, not the company.
const APPLY_RE = /appl(?:ying|ication)\s+(?:to|at|with)\s+(.+)$/i;

// Possessive lead: "<X>'s Recruiting Team", "<X>'s hiring team".
const POSSESSIVE_RE = /^(.+?)['’`]s\s+\S+/;

export function companyFromSubject(subject: string | null | undefined): string | null {
  if (!subject) return null;
  const s = subject.trim();
  if (!s) return null;

  const applyMatch = s.match(APPLY_RE);
  let candidate: string | null = applyMatch ? applyMatch[1] : null;

  if (!candidate) {
    const poss = s.match(POSSESSIVE_RE);
    if (poss) candidate = poss[1];
  }

  return candidate ? cleanCompany(candidate) : null;
}

function cleanCompany(raw: string): string | null {
  let c = raw.trim();

  // Cut role/qualifier tails at a separator: "Acme - Senior PM" → "Acme",
  // "Acme | Product" → "Acme", "Acme: Reqs" → "Acme".
  c = c.split(/\s+[-–—|:]\s+/)[0]?.trim() ?? c;

  // "Acme for the Product Manager role" → "Acme".
  c = c.replace(/\s+for\s+(?:the\s+|our\s+|a\s+)?.*$/i, '');

  // Drop a trailing role/qualifier noun and anything after it.
  c = c.replace(/\s+(position|role|opening|opportunity|req(?:uisition)?)\b.*$/i, '');

  // Trailing punctuation / whitespace ("Uphold!" → "Uphold").
  c = c.replace(/[\s!.,;:–—-]+$/, '').trim();

  // Leading article ("the Acme Team" already cut above; guard the simple case).
  c = c.replace(/^the\s+/i, '').trim();

  return c.length > 0 ? c : null;
}
