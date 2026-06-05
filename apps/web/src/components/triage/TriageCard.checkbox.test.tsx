import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { TriageCard } from '@/components/triage/TriageCard';
import type { PostingListItem } from '@/lib/triage/types';

// feat/bulk-triage-actions: the multi-select checkbox on the triage card.

function makePosting(): PostingListItem {
  return {
    id: 'p-1',
    company: { id: 'c-1', name: 'NoiseCo', domain: null, description: null, tier: 3 },
    role: {
      title: 'Customer Success Manager',
      family: 'other',
      department: null,
      team: null,
      seniority: null,
    },
    location_raw: 'Remote',
    locations_normalized: [],
    remote_type: 'remote',
    salary: null,
    source: { ats: 'ashby', url: null },
    first_seen_at: new Date().toISOString(),
    score: 30,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  };
}

const baseProps = {
  posting: makePosting(),
  isSelected: false,
  reasonOpen: false,
  onSelect: vi.fn(),
  onToggleReason: vi.fn(),
  onAction: vi.fn(),
};

describe('TriageCard checkbox', () => {
  test('no checkbox unless onToggleCheck is provided', () => {
    render(<TriageCard {...baseProps} />);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  test('renders a checked/unchecked checkbox and fires onToggleCheck', async () => {
    const user = userEvent.setup();
    const onToggleCheck = vi.fn();
    const onSelect = vi.fn();
    const { rerender } = render(
      <TriageCard
        {...baseProps}
        onSelect={onSelect}
        isChecked={false}
        onToggleCheck={onToggleCheck}
      />,
    );
    const box = screen.getByRole('checkbox');
    expect(box).not.toBeChecked();

    await user.click(box);
    expect(onToggleCheck).toHaveBeenCalledTimes(1);
    // The checkbox click must NOT also trigger card-select (stopPropagation).
    expect(onSelect).not.toHaveBeenCalled();

    rerender(
      <TriageCard {...baseProps} onSelect={onSelect} isChecked onToggleCheck={onToggleCheck} />,
    );
    expect(screen.getByRole('checkbox')).toBeChecked();
  });
});
