import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { Banner } from '@/components/chrome/Banner';
import { useUiStore } from '@/lib/stores/ui';

vi.mock('next/navigation', () => ({
  usePathname: () => '/',
}));

describe('Banner', () => {
  test('renders the title and subtitle passed via props', () => {
    render(<Banner title="Triage" subtitle="Pending review · 24" />);
    expect(screen.getByRole('heading', { name: /^Triage$/ })).toBeInTheDocument();
    expect(screen.getByText('Pending review · 24')).toBeInTheDocument();
  });

  test('the Jump to… button opens the command palette via the store', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ paletteOpen: false });
    render(<Banner title="Triage" />);
    // PR 1 UX overhaul: there are now two palette-trigger buttons —
    // an icon-only square at <sm and the full 280px bar at ≥sm,
    // chosen via responsive utilities. Clicking either dispatches
    // the same store action; assert by clicking the first match.
    const [trigger] = screen.getAllByRole('button', { name: /open command palette/i });
    await user.click(trigger);
    expect(useUiStore.getState().paletteOpen).toBe(true);
  });
});
