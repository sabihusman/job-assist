import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { TriageCard } from '@/components/triage/TriageCard';
import { avatarBg } from '@/lib/colors/avatar-hue';
import type { PostingListItem } from '@/lib/triage/types';

function makePosting(overrides: Partial<PostingListItem> = {}): PostingListItem {
  return {
    id: 'p-test-1',
    company: {
      id: 'c-1',
      name: 'TestCo',
      domain: 'testco.example',
      description: 'TestCo builds tests.',
      tier: 1,
    },
    role: {
      title: 'Senior PM',
      family: 'product_management',
      department: 'Product',
      team: 'Risk',
      seniority: 'senior_pm',
    },
    location_raw: 'San Francisco, CA',
    locations_normalized: ['San Francisco, CA'],
    remote_type: 'remote',
    salary: { min: 240000, max: 300000, currency: 'USD', period: 'annual' },
    source: { ats: 'greenhouse', url: 'https://example.test/job/1' },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
    ...overrides,
  };
}

describe('TriageCard', () => {
  test('renders company, role, and meta', () => {
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        onSelect={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByText('TestCo')).toBeInTheDocument();
    expect(screen.getByText('Senior PM')).toBeInTheDocument();
    expect(screen.getByText(/San Francisco, CA/)).toBeInTheDocument();
    expect(screen.getByText(/\$240k–\$300k/)).toBeInTheDocument();
  });

  test('selected card uses bg-primary on the tier strip', () => {
    const { rerender } = render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        onSelect={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByTestId('tier-strip').className).toContain('bg-tier-1');
    rerender(
      <TriageCard
        posting={makePosting()}
        isSelected={true}
        onSelect={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByTestId('tier-strip').className).toContain('bg-primary');
  });

  test('action 1 button calls onAction with kind="interested"', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        onSelect={() => {}}
        onAction={onAction}
      />,
    );
    const toolbar = screen.getByRole('toolbar', { name: /actions/i });
    await user.click(within(toolbar).getByLabelText(/Interested · 1/));
    expect(onAction).toHaveBeenCalledWith({ kind: 'interested' });
  });

  test('action 2 expands the inline reason picker', async () => {
    const user = userEvent.setup();
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        onSelect={() => {}}
        onAction={() => {}}
      />,
    );
    const toolbar = screen.getByRole('toolbar', { name: /actions/i });
    await user.click(within(toolbar).getByLabelText(/Pass · 2/));
    expect(screen.getByText(/why not\?/i)).toBeInTheDocument();
  });
});

describe('avatar hue helper', () => {
  test('same name produces the same oklch background', () => {
    expect(avatarBg('Linear')).toBe(avatarBg('Linear'));
  });
  test('different names produce different hues', () => {
    expect(avatarBg('Linear')).not.toBe(avatarBg('Stripe'));
  });
});
