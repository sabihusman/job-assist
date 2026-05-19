import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { ActionButton } from '@/components/shared/ActionButton';

describe('ActionButton', () => {
  test('compact variant renders the hotkey digit only', () => {
    render(<ActionButton variant="interested" size="compact" onClick={() => {}} />);
    const button = screen.getByLabelText(/Interested · 1/);
    expect(button.textContent).toBe('1');
  });

  test('full variant renders icon + label + hotkey kbd', () => {
    render(<ActionButton variant="applied" size="full" onClick={() => {}} />);
    expect(screen.getByText('Applied')).toBeInTheDocument();
    // Hotkey rendered as <kbd>.
    expect(screen.getByText('3').tagName).toBe('KBD');
  });
});
