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
  mercury: { rejections: 0, active_apps: 0, contact_count: 0, display_name: 'Mercury' },
  // feat/warm-path-badge fixtures
  'john deere': {
    rejections: 0,
    active_apps: 0,
    contact_count: 21,
    display_name: 'John Deere',
  },
  athene: { rejections: 1, active_apps: 1, contact_count: 3, display_name: 'Athene' },
  smithbucklin: {
    rejections: 0,
    active_apps: 0,
    contact_count: 1,
    display_name: 'Smithbucklin',
  },
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

  // ── feat/warm-path-badge ──────────────────────────────────────────────

  test('alumni badge renders for a contact-only company (positive, plural)', () => {
    render(<RepeatSignalBadges companyName="John Deere" signals={SIGNALS} />);
    const alumni = screen.getByText('21 alumni here');
    expect(alumni).toHaveAttribute('data-signal', 'alumni');
    expect(alumni.className).toContain('positive');
    // No false app/rejection badges alongside.
    expect(screen.queryByText(/active apps/)).not.toBeInTheDocument();
    expect(screen.queryByText(/rejection/)).not.toBeInTheDocument();
  });

  test('singular: 1 contact renders "1 alum here"', () => {
    render(<RepeatSignalBadges companyName="Smithbucklin" signals={SIGNALS} />);
    expect(screen.getByText('1 alum here')).toBeInTheDocument();
  });

  test('alumni badge shows alongside app + rejection badges', () => {
    render(<RepeatSignalBadges companyName="Athene" signals={SIGNALS} />);
    expect(screen.getByText('3 alumni here')).toBeInTheDocument();
    expect(screen.getByText('1 active apps')).toBeInTheDocument();
    expect(screen.getByText('1 rejection here')).toBeInTheDocument();
  });

  test('no contact_count (older payload) → no alumni badge, others unaffected', () => {
    render(<RepeatSignalBadges companyName="Stripe" signals={SIGNALS} />);
    expect(screen.queryByText(/alum/)).not.toBeInTheDocument();
    expect(screen.getByText('2 rejections here')).toBeInTheDocument();
  });

  test('default (no linkToContacts): alumni badge is NOT a link', () => {
    render(<RepeatSignalBadges companyName="John Deere" signals={SIGNALS} />);
    expect(screen.getByText('21 alumni here').tagName).toBe('SPAN');
  });

  test('linkToContacts: alumni badge links to /contacts?company=<display name>', () => {
    render(<RepeatSignalBadges companyName="John Deere" signals={SIGNALS} linkToContacts />);
    const alumni = screen.getByText('21 alumni here');
    expect(alumni.tagName).toBe('A');
    expect(alumni).toHaveAttribute('href', '/contacts?company=John%20Deere');
    // The other badges stay non-interactive even in link mode.
    render(<RepeatSignalBadges companyName="Brex" signals={SIGNALS} linkToContacts />);
    expect(screen.getByText('4 active apps').tagName).toBe('SPAN');
  });
});
