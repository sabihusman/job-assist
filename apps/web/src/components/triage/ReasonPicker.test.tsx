import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { REASON_CHOICES, ReasonPicker } from '@/components/triage/ReasonPicker';

describe('ReasonPicker', () => {
  test('renders all 10 chips with hotkey suffixes', () => {
    render(<ReasonPicker onSelect={() => {}} onCancel={() => {}} />);
    expect(REASON_CHOICES.length).toBe(10);
    for (const c of REASON_CHOICES) {
      expect(screen.getByText(c.label)).toBeInTheDocument();
    }
  });

  // feat/company-app-awareness: the portfolio pass (hotkey 0)
  test('chip 0 commits too_many_open_apps on click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.click(screen.getByText('Too many open apps here'));
    expect(onSelect).toHaveBeenCalledWith('too_many_open_apps');
  });

  test('hotkey 0 commits too_many_open_apps without any click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.keyboard('0');
    expect(onSelect).toHaveBeenCalledWith('too_many_open_apps');
  });

  // PR #43: too_senior / too_junior chips
  test('chip 8 commits too_senior on click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.click(screen.getByText('Too senior'));
    expect(onSelect).toHaveBeenCalledWith('too_senior');
  });

  test('chip 9 commits too_junior on click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.click(screen.getByText('Too junior'));
    expect(onSelect).toHaveBeenCalledWith('too_junior');
  });

  test('hotkey 8 commits too_senior without any click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.keyboard('8');
    expect(onSelect).toHaveBeenCalledWith('too_senior');
  });

  test('hotkey 9 commits too_junior without any click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ReasonPicker onSelect={onSelect} onCancel={() => {}} />);
    await user.keyboard('9');
    expect(onSelect).toHaveBeenCalledWith('too_junior');
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
