import { describe, expect, test } from 'vitest';

import { avatarBg, avatarInitial, hueFor } from '@/lib/colors/avatar-hue';

describe('hueFor', () => {
  test('deterministic for the same name', () => {
    expect(hueFor('Linear')).toBe(hueFor('Linear'));
  });

  test('case-insensitive', () => {
    expect(hueFor('Linear')).toBe(hueFor('linear'));
  });

  test('returns a value in [0, 360)', () => {
    for (const name of ['Linear', 'Vercel', 'Stripe', 'Notion', 'PostHog']) {
      const h = hueFor(name);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(360);
    }
  });
});

describe('avatarBg', () => {
  test('produces an oklch string', () => {
    expect(avatarBg('Linear')).toMatch(/^oklch\(0\.62 0\.13 \d+(\.\d+)?\)$/);
  });
});

describe('avatarInitial', () => {
  test('returns uppercase first character', () => {
    expect(avatarInitial('linear')).toBe('L');
    expect(avatarInitial('  vercel ')).toBe('V');
  });

  test('returns ? for empty input', () => {
    expect(avatarInitial('')).toBe('?');
    expect(avatarInitial('   ')).toBe('?');
  });
});
