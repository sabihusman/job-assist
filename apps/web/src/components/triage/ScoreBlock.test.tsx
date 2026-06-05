import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { ScoreBlock, isDimScore, scoreBand } from '@/components/triage/ScoreBlock';

describe('scoreBand', () => {
  test.each([
    [100, 'high'],
    [85, 'high'], // boundary — high starts at 85
    [84, 'mid'],
    [55, 'mid'],
    [41, 'mid'], // boundary — mid floor
    [40, 'low'], // boundary — low ceiling (also the dim threshold)
    [0, 'low'],
  ] as const)('scoreBand(%d) → %s', (score, expected) => {
    expect(scoreBand(score)).toBe(expected);
  });
});

describe('isDimScore', () => {
  test.each([
    [40, true],
    [0, true],
    [41, false],
    [90, false],
  ] as const)('isDimScore(%d) → %s', (score, expected) => {
    expect(isDimScore(score)).toBe(expected);
  });

  test('null / undefined are not dim', () => {
    expect(isDimScore(null)).toBe(false);
    expect(isDimScore(undefined)).toBe(false);
  });
});

describe('ScoreBlock', () => {
  test('renders the score and the band on data-band', () => {
    render(<ScoreBlock score={88} />);
    const block = screen.getByTestId('score-block');
    expect(block).toHaveTextContent('88');
    expect(block.getAttribute('data-band')).toBe('high');
    expect(block.getAttribute('aria-label')).toBe('Fit score: 88 out of 100');
  });

  test('null score renders an em-dash, never a misleading 0', () => {
    render(<ScoreBlock score={null} />);
    const block = screen.getByTestId('score-block');
    expect(block).toHaveTextContent('—');
    expect(block).not.toHaveTextContent('0');
    expect(block.getAttribute('data-band')).toBe('none');
    expect(block.getAttribute('aria-label')).toMatch(/not yet scored/i);
  });

  test('showLabel renders the "fit score" caption (detail header)', () => {
    render(<ScoreBlock score={70} size="lg" showLabel />);
    expect(screen.getByText('fit score')).toBeInTheDocument();
  });

  test('no caption by default (card rail)', () => {
    render(<ScoreBlock score={70} />);
    expect(screen.queryByText('fit score')).not.toBeInTheDocument();
  });
});
