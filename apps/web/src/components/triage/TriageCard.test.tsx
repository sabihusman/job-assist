import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, test, vi } from 'vitest';

import { TriageCard, type TriageCardAction } from '@/components/triage/TriageCard';
import { avatarBg } from '@/lib/colors/avatar-hue';
import type { PostingListItem } from '@/lib/triage/types';

/**
 * Controlled-state wrapper used by tests that exercise the
 * Pass-button click → picker-open path (PR #47 lifted the state out
 * of TriageCard, so an external host has to drive it).
 */
function ControlledTriageCard({
  posting,
  isSelected,
  onAction,
}: {
  posting: PostingListItem;
  isSelected: boolean;
  onAction: (a: TriageCardAction) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <TriageCard
      posting={posting}
      isSelected={isSelected}
      reasonOpen={open}
      onSelect={() => {}}
      onToggleReason={() => setOpen((v) => !v)}
      onAction={onAction}
    />
  );
}

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
    render(<ControlledTriageCard posting={makePosting()} isSelected={false} onAction={() => {}} />);
    expect(screen.getByText('TestCo')).toBeInTheDocument();
    expect(screen.getByText('Senior PM')).toBeInTheDocument();
    expect(screen.getByText(/San Francisco, CA/)).toBeInTheDocument();
    expect(screen.getByText(/\$240k–\$300k/)).toBeInTheDocument();
  });

  test('selected card uses bg-primary on the tier strip', () => {
    const { rerender } = render(
      <ControlledTriageCard posting={makePosting()} isSelected={false} onAction={() => {}} />,
    );
    expect(screen.getByTestId('tier-strip').className).toContain('bg-tier-1');
    rerender(
      <ControlledTriageCard posting={makePosting()} isSelected={true} onAction={() => {}} />,
    );
    expect(screen.getByTestId('tier-strip').className).toContain('bg-primary');
  });

  test('action 1 button calls onAction with kind="interested"', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(<ControlledTriageCard posting={makePosting()} isSelected={false} onAction={onAction} />);
    const toolbar = screen.getByRole('toolbar', { name: /actions/i });
    await user.click(within(toolbar).getByLabelText(/Interested · 1/));
    expect(onAction).toHaveBeenCalledWith({ kind: 'interested' });
  });

  test('action 2 button toggles the inline reason picker', async () => {
    const user = userEvent.setup();
    render(<ControlledTriageCard posting={makePosting()} isSelected={false} onAction={() => {}} />);
    const toolbar = screen.getByRole('toolbar', { name: /actions/i });
    await user.click(within(toolbar).getByLabelText(/Pass · 2/));
    expect(screen.getByText(/why not\?/i)).toBeInTheDocument();
  });

  // ── PR #47: reasonOpen prop controls picker visibility ────────────────

  test('reasonOpen=false renders no picker', () => {
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        reasonOpen={false}
        onSelect={() => {}}
        onToggleReason={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.queryByText(/why not\?/i)).not.toBeInTheDocument();
  });

  test('reasonOpen=true renders the picker without any click', () => {
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        reasonOpen={true}
        onSelect={() => {}}
        onToggleReason={() => {}}
        onAction={() => {}}
      />,
    );
    // Picker chrome.
    expect(screen.getByText(/why not\?/i)).toBeInTheDocument();
    // All 9 reason chips (PR #45 vocabulary).
    expect(screen.getByText('Wrong role')).toBeInTheDocument();
    expect(screen.getByText('Too senior')).toBeInTheDocument();
    expect(screen.getByText('Too junior')).toBeInTheDocument();
  });

  test('picker onSelect calls onToggleReason then onAction', async () => {
    const user = userEvent.setup();
    const onToggleReason = vi.fn();
    const onAction = vi.fn();
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        reasonOpen={true}
        onSelect={() => {}}
        onToggleReason={onToggleReason}
        onAction={onAction}
      />,
    );
    await user.click(screen.getByText('Comp too low'));
    expect(onToggleReason).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith({ kind: 'not_interested', reason: 'comp_too_low' });
  });

  test('picker onCancel (Esc) calls onToggleReason without committing', async () => {
    const user = userEvent.setup();
    const onToggleReason = vi.fn();
    const onAction = vi.fn();
    render(
      <TriageCard
        posting={makePosting()}
        isSelected={false}
        reasonOpen={true}
        onSelect={() => {}}
        onToggleReason={onToggleReason}
        onAction={onAction}
      />,
    );
    await user.keyboard('{Escape}');
    expect(onToggleReason).toHaveBeenCalledTimes(1);
    expect(onAction).not.toHaveBeenCalled();
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
