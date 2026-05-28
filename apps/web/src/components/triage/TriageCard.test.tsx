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

// ── PR 2 UX overhaul: row reshuffle + status pill ─────────────────────

describe('TriageCard PR 2 layout', () => {
  test('FitScoreBadge with a numeric score renders on row 1, not the meta row', () => {
    const posting = makePosting({ score: 91 });
    render(<ControlledTriageCard posting={posting} isSelected={false} onAction={() => {}} />);
    // The badge surfaces an aria-label "Fit score 91" via FitScoreBadge.
    // PR 2 contract: it must NOT live in the same flex cluster as the
    // location row — assert it's a sibling of the company name.
    const badge = screen.getByLabelText(/fit score/i);
    const companyName = screen.getByText('TestCo');
    // Walk up to a shared ancestor that holds both — they should share
    // an immediate flex row at the top of the card (the row 1 wrapper).
    const row1 = companyName.closest('div')?.parentElement;
    expect(row1).not.toBeNull();
    expect(row1).toContainElement(badge);
    // The location row should NOT contain the badge anymore.
    const locationText = screen.getByText('San Francisco, CA');
    const locationRow = locationText.closest('div');
    expect(locationRow).not.toContainElement(badge);
  });

  test('status pill renders only when posting.state.current is set', () => {
    const withState = makePosting({
      state: { current: 'applied', reason: null, snooze_until: null, current_at: null },
    });
    const { rerender } = render(
      <ControlledTriageCard posting={withState} isSelected={false} onAction={() => {}} />,
    );
    expect(screen.getByTestId('status-pill')).toBeInTheDocument();
    expect(screen.getByTestId('status-pill').textContent).toBe('APP');

    // Re-render with no state → no pill.
    const blank = makePosting();
    rerender(<ControlledTriageCard posting={blank} isSelected={false} onAction={() => {}} />);
    expect(screen.queryByTestId('status-pill')).toBeNull();
  });

  test('role title is truncated with a title= attribute for hover disambiguation', () => {
    const posting = makePosting({
      role: { ...makePosting().role, title: 'Very Long Role Title That Will Truncate Visually' },
    });
    render(<ControlledTriageCard posting={posting} isSelected={false} onAction={() => {}} />);
    const titleSpan = screen.getByText(/Very Long Role Title/);
    expect(titleSpan.getAttribute('title')).toBe(
      'Very Long Role Title That Will Truncate Visually',
    );
    expect(titleSpan.className).toMatch(/truncate/);
  });

  test('action column still renders all four ActionButton variants (regression lock)', () => {
    // PR 2 row reshuffle touched the body layout; the action column is
    // a sibling and must remain intact.
    const posting = makePosting();
    render(<ControlledTriageCard posting={posting} isSelected={false} onAction={() => {}} />);
    const toolbar = screen.getByRole('toolbar', { name: 'Actions' });
    expect(within(toolbar).getAllByRole('button')).toHaveLength(4);
  });
});
