import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { CommandPalette } from '@/components/chrome/CommandPalette';
import { useUiStore } from '@/lib/stores/ui';

const pushMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock('sonner', () => ({
  toast: vi.fn(),
}));

beforeEach(() => {
  useUiStore.setState({ paletteOpen: true });
  pushMock.mockClear();
});

afterEach(() => {
  useUiStore.setState({ paletteOpen: false });
});

describe('CommandPalette', () => {
  test('does not list Outreach as a destination', () => {
    render(<CommandPalette />);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByText(/go to outreach/i)).toBeNull();
  });

  test('filters items by substring match (case-insensitive)', async () => {
    const user = userEvent.setup();
    render(<CommandPalette />);
    const input = screen.getByPlaceholderText(/search commands/i);
    await user.type(input, 'stats');
    expect(screen.getByText(/go to stats/i)).toBeInTheDocument();
    // Pipeline shouldn't survive the "stats" filter.
    expect(screen.queryByText(/go to pipeline/i)).toBeNull();
  });

  test('Enter on a Go-to item routes through next/navigation', async () => {
    const user = userEvent.setup();
    render(<CommandPalette />);
    const input = screen.getByPlaceholderText(/search commands/i);
    await user.type(input, 'applied');
    await user.keyboard('{Enter}');
    expect(pushMock).toHaveBeenCalledWith('/applied');
  });

  test("renders 'No matches' on gibberish search", async () => {
    const user = userEvent.setup();
    render(<CommandPalette />);
    await user.type(screen.getByPlaceholderText(/search commands/i), 'zzzzzzz');
    expect(screen.getByText(/no matches/i)).toBeInTheDocument();
  });
});
