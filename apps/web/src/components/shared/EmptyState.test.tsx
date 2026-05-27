import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { EmptyState } from '@/components/shared/EmptyState';

/**
 * EmptyState primitive contract (UX overhaul PR 1).
 *
 * Six tests pin the shape so the 11 ad-hoc page-local implementations
 * can migrate to this primitive in PR 2/PR 3 without regressing
 * accessibility or visual landmarks.
 */
describe('EmptyState', () => {
  test('renders title as an h2', () => {
    render(<EmptyState title="No passed postings yet." />);
    expect(
      screen.getByRole('heading', { level: 2, name: 'No passed postings yet.' }),
    ).toBeInTheDocument();
  });

  test('renders description when provided', () => {
    render(<EmptyState title="No data" description="Try a wider filter." />);
    expect(screen.getByText('Try a wider filter.')).toBeInTheDocument();
  });

  test('omits description paragraph when not provided', () => {
    render(<EmptyState title="Empty" />);
    // The only paragraph in the absence of description would be the
    // description itself — assert there's no <p>.
    expect(screen.queryByText((_, el) => el?.tagName === 'P')).toBeNull();
  });

  test('renders action slot when provided', () => {
    render(
      <EmptyState
        title="No filters match"
        action={
          <button type="button" data-testid="reset-btn">
            Reset
          </button>
        }
      />,
    );
    expect(screen.getByTestId('reset-btn')).toBeInTheDocument();
  });

  test('forwards testId to the outer section', () => {
    render(<EmptyState title="x" testId="passed-empty" />);
    expect(screen.getByTestId('passed-empty')).toBeInTheDocument();
  });

  test('renders icon slot above title when provided', () => {
    render(<EmptyState title="x" icon={<span data-testid="empty-icon">★</span>} />);
    expect(screen.getByTestId('empty-icon')).toBeInTheDocument();
  });
});
