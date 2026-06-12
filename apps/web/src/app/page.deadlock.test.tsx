import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import TriagePage from '@/app/page';

/**
 * fix(audit): deep-linked-posting keyboard deadlock regression.
 *
 * Arriving via /?posting=<id> (the Pipeline crosslink) selects a posting that
 * may not be IN the current triage list. Pressing '2' used to set
 * reasonPickerCardId to that id — no card rendered the picker, yet
 * useTriageKeyboard was paused because reasonPickerCardId !== null. J/K, 1-4,
 * AND Escape all went dead (Escape's handler lives in the paused hook), with
 * recovery only via a mouse round-trip. The fix: '2' only opens the list
 * picker when the selected posting is actually in the list.
 *
 * Heavy children are stubbed; the REAL useTriageKeyboard + page wiring are
 * under test. The TriageList stub mirrors the props the page drives so the
 * assertions can read selection / picker state from the DOM.
 */

// One STABLE instance (vi.hoisted — mock factories hoist above consts). The
// page memoizes parseFilters on the searchParams identity; a fresh object per
// render would re-trigger the filters-change effect every render and loop.
const { stableParams, stableRouter } = vi.hoisted(() => ({
  stableParams: new URLSearchParams('posting=ghost-not-in-list'),
  stableRouter: { replace: vi.fn(), push: vi.fn() },
}));
vi.mock('next/navigation', () => ({
  useSearchParams: () => stableParams,
  useRouter: () => stableRouter,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api/error-toast', () => ({ showErrorToast: vi.fn() }));

vi.mock('@/components/chrome/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/triage/FilterRow', () => ({ FilterRow: () => null }));
vi.mock('@/components/triage/CalibrationCard', () => ({ CalibrationCard: () => null }));
vi.mock('@/components/triage/BulkActionBar', () => ({ BulkActionBar: () => null }));
vi.mock('@/components/triage/DetailPanel', () => ({ DetailPanel: () => null }));
vi.mock('@/components/triage/TriageList', () => ({
  TriageList: ({
    selectedIndex,
    reasonPickerCardId,
  }: {
    selectedIndex: number | null;
    reasonPickerCardId: string | null;
  }) => (
    <div
      data-testid="triage-list-stub"
      data-selected-index={selectedIndex === null ? 'null' : String(selectedIndex)}
      data-reason-picker={reasonPickerCardId ?? 'null'}
    />
  ),
}));

vi.mock('@/lib/api/companySignals', () => ({ useCompanySignals: () => ({ data: undefined }) }));

const items = [
  { id: 'p-1', score: 90 },
  { id: 'p-2', score: 85 },
];

vi.mock('@/lib/api/hooks', () => ({
  useTriagePostingsInfinite: () => ({
    items,
    total: 2,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    data: { pages: [{ total: 2, items }] },
  }),
  // The applied-count subtitle query.
  useTriagePostings: () => ({ data: { total: 0, items: [] }, isError: false, isLoading: false }),
  useRecordAction: () => ({ mutate: vi.fn(), isPending: false }),
  useBulkRecordAction: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe('triage keyboard with a deep-linked posting not in the list', () => {
  test("pressing '2' does not deadlock — no phantom picker, J still navigates", async () => {
    render(<TriagePage />);
    const stub = await screen.findByTestId('triage-list-stub');

    // Deep link suppresses the row-0 auto-select: nothing selected in-list.
    expect(stub).toHaveAttribute('data-selected-index', 'null');

    // The old bug: '2' set reasonPickerCardId='ghost-not-in-list' (no card
    // renders it) and the keyboard went dead. Now: no picker opens...
    fireEvent.keyDown(window, { key: '2' });
    expect(stub).toHaveAttribute('data-reason-picker', 'null');

    // ...and the keyboard is still alive — J selects the first row.
    fireEvent.keyDown(window, { key: 'j' });
    expect(stub).toHaveAttribute('data-selected-index', '0');

    // Sanity: '2' on an in-list selection still opens that card's picker.
    fireEvent.keyDown(window, { key: '2' });
    expect(stub).toHaveAttribute('data-reason-picker', 'p-1');
  });
});
