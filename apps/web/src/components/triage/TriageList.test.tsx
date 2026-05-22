import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { TriageList } from '@/components/triage/TriageList';
import type { PostingListItem } from '@/lib/triage/types';

function makePostings(n: number): PostingListItem[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `p-${i}`,
    company: {
      id: `c-${i}`,
      name: `Co${i}`,
      domain: null,
      description: null,
      tier: 2,
    },
    role: {
      title: `Role ${i}`,
      family: null,
      department: null,
      team: null,
      seniority: null,
    },
    location_raw: null,
    locations_normalized: [],
    remote_type: null,
    salary: null,
    source: { ats: 'greenhouse', url: null },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
  }));
}

describe('TriageList', () => {
  test('renders one TriageCard per posting', () => {
    render(
      <TriageList
        postings={makePostings(3)}
        selectedIndex={null}
        reasonPickerCardId={null}
        onSelect={() => {}}
        onToggleReason={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  test('calls onSelect with the clicked index', async () => {
    const onSelect = vi.fn();
    const user = (await import('@testing-library/user-event')).default.setup();
    render(
      <TriageList
        postings={makePostings(3)}
        selectedIndex={null}
        reasonPickerCardId={null}
        onSelect={onSelect}
        onToggleReason={() => {}}
        onAction={() => {}}
      />,
    );
    await user.click(screen.getByLabelText(/Open detail for Co1/));
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  // ── PR #47: reasonPickerCardId targets exactly one card ─────────────────
  test('reasonPickerCardId targets exactly one card', () => {
    render(
      <TriageList
        postings={makePostings(3)}
        selectedIndex={null}
        reasonPickerCardId="p-1"
        onSelect={() => {}}
        onToggleReason={() => {}}
        onAction={() => {}}
      />,
    );
    // Picker mounts on exactly one card (its "Why not?" chrome).
    expect(screen.getAllByText(/why not\?/i)).toHaveLength(1);
  });

  test('reasonPickerCardId=null mounts no pickers', () => {
    render(
      <TriageList
        postings={makePostings(3)}
        selectedIndex={null}
        reasonPickerCardId={null}
        onSelect={() => {}}
        onToggleReason={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.queryByText(/why not\?/i)).not.toBeInTheDocument();
  });
});
