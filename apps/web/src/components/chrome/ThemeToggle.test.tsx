import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider } from 'next-themes';
import { beforeEach, describe, expect, test } from 'vitest';

import { ThemeToggle } from '@/components/chrome/ThemeToggle';

function renderWithTheme() {
  return render(
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.className = '';
});

describe('ThemeToggle', () => {
  test('clicking Dark adds the .dark class to <html>', async () => {
    const user = userEvent.setup();
    renderWithTheme();
    await user.click(screen.getByRole('button', { name: /^dark$/i }));
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(true));
  });

  test('dark choice persists to localStorage under the next-themes key', async () => {
    const user = userEvent.setup();
    renderWithTheme();
    await user.click(screen.getByRole('button', { name: /^dark$/i }));
    await waitFor(() => expect(window.localStorage.getItem('theme')).toBe('dark'));
  });
});
