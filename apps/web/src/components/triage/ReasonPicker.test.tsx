import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { REASON_CHOICES, ReasonPicker } from '@/components/triage/ReasonPicker';

describe('ReasonPicker', () => {
  test('renders all 7 chips with hotkey suffixes', () => {
    render(<ReasonPicker onSelect={() => {}} onCancel={() => {}} />);
    for (const c of REASON_CHOICES) {
      expect(screen.getByText(c.label)).toBeInTheDocument();
    }
  });

  test('clicking a chip calls onSelect with the correct reason', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.click(screen.getByText('Comp too low'));
    expect(onSelect).toHaveBeenCalledWith('comp_too_low');
  });

  test('Esc keystroke calls onCancel', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<ReasonPicker onSelect={() => {}} onCancel={onCancel} />);
    await user.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalled();
  });

  test('hotkey 3 commits comp_too_low without any click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.keyboard('3');
    expect(onSelect).toHaveBeenCalledWith('comp_too_low');
  });
});
