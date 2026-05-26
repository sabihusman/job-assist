import { render, screen, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { ApiKeysSection } from '@/components/settings/ApiKeysSection';

describe('ApiKeysSection', () => {
  test('renders 5 hardcoded env-var rows with "set" status', () => {
    render(<ApiKeysSection />);
    for (const name of [
      'DATABASE_URL',
      'GEMINI_API_KEY',
      'ANTHROPIC_API_KEY',
      'GMAIL_CREDENTIALS_JSON',
      'GMAIL_REFRESH_TOKEN',
    ]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getAllByText('set')).toHaveLength(5);
  });

  test('does not render a "missing" status or "Week 4" tag', () => {
    render(<ApiKeysSection />);
    expect(screen.queryByText(/missing/i)).toBeNull();
    expect(screen.queryByText(/week 4/i)).toBeNull();
  });

  test('every row pairs the env name with a set badge in the same row', () => {
    render(<ApiKeysSection />);
    const dbRow = screen.getByText('DATABASE_URL').closest('li');
    if (!dbRow) throw new Error('DATABASE_URL row not found');
    expect(within(dbRow).getByText('set')).toBeInTheDocument();
  });
});
