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
    await user.click(screen.getByRole('button', { name: /open command palette/i }));
    expect(useUiStore.getState().paletteOpen).toBe(true);
  });
});
