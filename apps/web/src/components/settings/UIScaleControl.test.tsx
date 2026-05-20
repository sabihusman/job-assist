import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test } from 'vitest';

import { UIScaleControl } from '@/components/settings/UIScaleControl';

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.style.fontSize = '';
});

describe('UIScaleControl', () => {
  test('increment + decrement update the persisted percent', async () => {
    const user = userEvent.setup();
    render(<UIScaleControl />);
    await waitFor(() => expect(screen.getByText('+0%')).toBeInTheDocument());
    await user.click(screen.getByLabelText(/increase ui scale/i));
    expect(screen.getByText('+2%')).toBeInTheDocument();
    await user.click(screen.getByLabelText(/increase ui scale/i));
    expect(screen.getByText('+4%')).toBeInTheDocument();
    await user.click(screen.getByLabelText(/decrease ui scale/i));
    expect(screen.getByText('+2%')).toBeInTheDocument();
    await waitFor(() => expect(window.localStorage.getItem('ui-scale-pct')).toBe('2'));
  });

  test('decrement clamps at 0', async () => {
    const user = userEvent.setup();
    render(<UIScaleControl />);
    await waitFor(() => expect(screen.getByText('+0%')).toBeInTheDocument());
    // Decrement button is disabled at 0.
    expect(screen.getByLabelText(/decrease ui scale/i)).toBeDisabled();
    await user.click(screen.getByLabelText(/decrease ui scale/i));
    expect(screen.getByText('+0%')).toBeInTheDocument();
  });

  test('reset returns to 0 from any value', async () => {
    const user = userEvent.setup();
    window.localStorage.setItem('ui-scale-pct', '10');
    render(<UIScaleControl />);
    // "+10%" also appears as a static tick label below the slider —
    // scope to the live-region readout via its aria-live attribute.
    const readout = await screen.findByText('+10%', {
      selector: '[aria-live="polite"]',
    });
    expect(readout).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'reset' }));
    expect(screen.getByText('+0%', { selector: '[aria-live="polite"]' })).toBeInTheDocument();
  });

  test('applies font-size to the document root', async () => {
    const user = userEvent.setup();
    render(<UIScaleControl />);
    await waitFor(() => expect(screen.getByText('+0%')).toBeInTheDocument());
    await user.click(screen.getByLabelText(/increase ui scale/i));
    await waitFor(() => expect(document.documentElement.style.fontSize).toBe('102%'));
  });
});
