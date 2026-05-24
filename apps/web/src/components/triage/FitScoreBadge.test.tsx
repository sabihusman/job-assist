import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { FitScoreBadge, toneForScore } from '@/components/triage/FitScoreBadge';

describe('FitScoreBadge', () => {
  // ── NULL handling ──────────────────────────────────────────────────────

  test('renders nothing when score is null', () => {
    const { container } = render(<FitScoreBadge score={null} />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId('fit-score-badge')).toBeNull();
  });

  // ── Visible cases ──────────────────────────────────────────────────────

  test('renders the numeric score with no prefix', () => {
    render(<FitScoreBadge score={78} />);
    const badge = screen.getByTestId('fit-score-badge');
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toBe('78');
    // Spec: no "Fit:" prefix in the visible text — saves ~30px on mobile.
    expect(badge.textContent).not.toContain('Fit');
  });

  test('aria-label reads as a complete sentence with "out of 100"', () => {
    render(<FitScoreBadge score={78} />);
    const badge = screen.getByLabelText('Fit score: 78 out of 100');
    expect(badge).toBeInTheDocument();
  });

  test('title attribute matches the aria-label for mouse-hover discovery', () => {
    render(<FitScoreBadge score={78} />);
    const badge = screen.getByTestId('fit-score-badge');
    expect(badge.getAttribute('title')).toBe('Fit score: 78 out of 100');
  });

  // ── Bucket lookup ──────────────────────────────────────────────────────

  test.each([
    [0, 'muted-dim'],
    [19, 'muted-dim'],
    [20, 'muted-dim'],
    [39, 'muted-dim'],
    [40, 'muted'],
    [59, 'muted'],
    [60, 'pending'],
    [79, 'pending'],
    [80, 'positive'],
    [100, 'positive'],
  ] as const)('toneForScore(%d) → %s', (score, expected) => {
    expect(toneForScore(score)).toBe(expected);
  });

  // Positive-equality colour assertions — read the rendered class string
  // and check the expected token is present. (We don't assert "X is NOT
  // in the class string" per the bestiary "positive equality only" rule.)
  test('renders positive tokens for score 90', () => {
    render(<FitScoreBadge score={90} />);
    const badge = screen.getByTestId('fit-score-badge');
    expect(badge.className).toContain('text-positive');
  });

  test('renders pending tokens for score 70', () => {
    render(<FitScoreBadge score={70} />);
    const badge = screen.getByTestId('fit-score-badge');
    expect(badge.className).toContain('text-pending');
  });

  test('renders muted tokens for score 50', () => {
    render(<FitScoreBadge score={50} />);
    const badge = screen.getByTestId('fit-score-badge');
    expect(badge.className).toContain('text-muted-foreground');
  });

  test('renders dimmed muted tokens for score 20', () => {
    render(<FitScoreBadge score={20} />);
    const badge = screen.getByTestId('fit-score-badge');
    // The 0-39 bucket uses the muted palette with reduced opacity.
    expect(badge.className).toContain('text-muted-foreground/70');
  });
});
