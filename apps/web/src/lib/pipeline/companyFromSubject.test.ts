import { describe, expect, test } from 'vitest';

import { companyFromSubject } from '@/lib/pipeline/companyFromSubject';

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
});
