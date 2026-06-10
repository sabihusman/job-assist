import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { RepeatSignalBadges } from '@/components/shared/RepeatSignalBadges';
import type { RepeatSignals } from '@/lib/api/companySignals';

// Keyed by NORMALIZED company name (feat/company-app-awareness).
const SIGNALS: RepeatSignals = {
  stripe: { rejections: 2, active_apps: 1, display_name: 'Stripe' },
  ramp: { rejections: 0, active_apps: 2, display_name: 'Ramp' },
  plaid: { rejections: 0, active_apps: 3, display_name: 'Plaid' },
  brex: { rejections: 1, active_apps: 4, display_name: 'Brex' },
  mercury: { rejections: 0, active_apps: 0, display_name: 'Mercury' },
};

describe('RepeatSignalBadges', () => {
  test('renders nothing for a company with no signal', () => {
    const { container } = render(<RepeatSignalBadges companyName="Unknown Co" signals={SIGNALS} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders nothing when companyName is null', () => {
    const { container } = render(<RepeatSignalBadges companyName={null} signals={SIGNALS} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders nothing when signals are undefined (not yet loaded)', () => {
    const { container } = render(<RepeatSignalBadges companyName="Stripe" signals={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders nothing when both counts are zero', () => {
    const { container } = render(<RepeatSignalBadges companyName="Mercury" signals={SIGNALS} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('matches by NORMALIZED name — "Stripe, Inc." resolves to "stripe"', () => {
    render(<RepeatSignalBadges companyName="Stripe, Inc." signals={SIGNALS} />);
    expect(screen.getByText('2 rejections here')).toBeInTheDocument();
    // active_apps = 1 → neutral, no amber.
    const active = screen.getByText('1 active apps');
    expect(active).toHaveAttribute('data-signal', 'neutral');
  });

  test('1–2 active apps → NEUTRAL (not amber)', () => {
    render(<RepeatSignalBadges companyName="Ramp" signals={SIGNALS} />);
    const active = screen.getByText('2 active apps');
    expect(active).toHaveAttribute('data-signal', 'neutral');
    expect(active.className).not.toContain('amber');
    expect(screen.queryByText(/rejection/)).not.toBeInTheDocument();
  });

  test('exactly 3 active apps → AMBER', () => {
    render(<RepeatSignalBadges companyName="Plaid" signals={SIGNALS} />);
    const active = screen.getByText('3 active apps');
    expect(active).toHaveAttribute('data-signal', 'amber');
    expect(active.className).toContain('amber');
  });

  test('≥3 active apps amber + rejections shown alongside (neutral)', () => {
    render(<RepeatSignalBadges companyName="Brex" signals={SIGNALS} />);
    const active = screen.getByText('4 active apps');
    expect(active).toHaveAttribute('data-signal', 'amber');
    const rej = screen.getByText('1 rejection here'); // singular
    expect(rej).toHaveAttribute('data-signal', 'rejections');
    expect(rej.className).not.toContain('amber');
  });
});
