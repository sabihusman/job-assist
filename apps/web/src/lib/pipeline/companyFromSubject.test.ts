import { describe, expect, test } from 'vitest';

import { companyFromSubject, roleFromSubject } from '@/lib/pipeline/companyFromSubject';

describe('companyFromSubject', () => {
  test('extracts company from the dominant "applying to <X>" pattern', () => {
    expect(companyFromSubject('Thank you for applying to Solv Health')).toBe('Solv Health');
  });

  test('is case-insensitive', () => {
    expect(companyFromSubject('Thank You for Applying to Goldman Sachs')).toBe('Goldman Sachs');
  });

  test('handles "applying at <X>" and strips trailing punctuation', () => {
    expect(companyFromSubject('Thank you for applying at Uphold!')).toBe('Uphold');
  });

  test('strips a trailing role after a separator', () => {
    expect(companyFromSubject('Thank you for applying to Ramp - Senior PM')).toBe('Ramp');
    expect(companyFromSubject('Applying to Plaid | Product Manager')).toBe('Plaid');
  });

  test('strips a trailing "for the <role>" tail', () => {
    expect(companyFromSubject('Thank you for applying to Stripe for the Product role')).toBe(
      'Stripe',
    );
  });

  test('handles the possessive "<X>\'s <team>" pattern', () => {
    expect(companyFromSubject("Altruist's Recruiting Team")).toBe('Altruist');
  });

  test('returns null for a generic subject (caller falls back)', () => {
    expect(companyFromSubject('Update on Your Application')).toBeNull();
    expect(companyFromSubject('Application received')).toBeNull();
  });

  test('returns null for empty / nullish input', () => {
    expect(companyFromSubject('')).toBeNull();
    expect(companyFromSubject(null)).toBeNull();
    expect(companyFromSubject(undefined)).toBeNull();
  });

  // ── fix(audit): the exact failing cases from the audit ─────────────────────

  test('separator with no leading space still splits ("Acme: Reqs" → "Acme")', () => {
    // cleanCompany's own documented example used to fail: the split required
    // whitespace BEFORE the separator, so the full string shipped as the label.
    expect(companyFromSubject('Thank you for applying to Acme: Senior Product Manager')).toBe(
      'Acme',
    );
    expect(companyFromSubject('Thank you for applying to Acme- Senior PM')).toBe('Acme');
  });

  test('hyphenated company names do not split on their own hyphen', () => {
    expect(companyFromSubject('Thank you for applying to Coca-Cola')).toBe('Coca-Cola');
  });

  test('non-leading possessives return null instead of junk labels', () => {
    // The lazy prefix used to capture "An update from Acme" / "Your
    // application" — junk that ranked above the from_domain fallback.
    expect(companyFromSubject("An update from Acme's Recruiting Team")).toBeNull();
    expect(companyFromSubject("Your application's status has been updated")).toBeNull();
  });

  test('multi-token company possessives still extract', () => {
    expect(companyFromSubject("Greenhouse Software's hiring team")).toBe('Greenhouse Software');
  });
});

describe('roleFromSubject', () => {
  test('extracts a role from a trailing segment', () => {
    expect(roleFromSubject('Covr Financial Technologies - Jr. Product Manager')).toBe(
      'Jr. Product Manager',
    );
    expect(roleFromSubject('Application received: Stripe — Staff Product Manager')).toBe(
      'Staff Product Manager',
    );
  });

  test('returns null when no role is present (the ~77% case) — role is omitted', () => {
    expect(roleFromSubject('Thank you for applying to Goldman Sachs')).toBeNull();
    expect(roleFromSubject('Application Received')).toBeNull();
    expect(roleFromSubject('Thanks for applying to Stripe!')).toBeNull();
  });

  test('does not mistake apply-confirmation boilerplate for a role', () => {
    // "Project Management" keyword present, but the segment is boilerplate.
    expect(roleFromSubject('Thank you for applying to Acme')).toBeNull();
  });

  test('returns null for empty / nullish input', () => {
    expect(roleFromSubject('')).toBeNull();
    expect(roleFromSubject(null)).toBeNull();
    expect(roleFromSubject(undefined)).toBeNull();
  });
});
