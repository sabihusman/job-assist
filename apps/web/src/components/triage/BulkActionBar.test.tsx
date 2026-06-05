import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { BulkActionBar } from '@/components/triage/BulkActionBar';

function setup(overrides: Partial<Parameters<typeof BulkActionBar>[0]> = {}) {
  const props = {
    selectedCount: 0,
    visibleCount: 10,
    lowScoreCount: 6,
    lowScoreThreshold: 40,
    busy: false,
    onSelectLowScore: vi.fn(),
    onSelectAllVisible: vi.fn(),
    onClear: vi.fn(),
    onPass: vi.fn(),
    onReset: vi.fn(),
    ...overrides,
  };
  render(<BulkActionBar {...props} />);
  return props;
}

describe('BulkActionBar', () => {
  test('select shortcuts show counts and fire their callbacks', async () => {
    const user = userEvent.setup();
    const p = setup();
    await user.click(screen.getByRole('button', { name: /select ≤40 \(6\)/i }));
    await user.click(screen.getByRole('button', { name: /select all visible \(10\)/i }));
    expect(p.onSelectLowScore).toHaveBeenCalledTimes(1);
    expect(p.onSelectAllVisible).toHaveBeenCalledTimes(1);
  });

  test('"Select ≤N" is disabled when the cohort is empty', () => {
    setup({ lowScoreCount: 0 });
    expect(screen.getByRole('button', { name: /select ≤40 \(0\)/i })).toBeDisabled();
  });

  test('no action controls when nothing is selected', () => {
    setup({ selectedCount: 0 });
    expect(screen.queryByRole('button', { name: /^pass/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^reset/i })).not.toBeInTheDocument();
  });

  test('Pass is two-step: needs an explicit Confirm before onPass fires', async () => {
    const user = userEvent.setup();
    const p = setup({ selectedCount: 5 });

    expect(screen.getByText('5 selected')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Pass 5' }));
    // Not yet — the confirm step is showing.
    expect(p.onPass).not.toHaveBeenCalled();
    expect(screen.getByText(/pass 5 roles\?/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(p.onPass).toHaveBeenCalledTimes(1);
    // Default reason.
    expect(p.onPass).toHaveBeenCalledWith('wrong_role');
  });

  test('Cancel backs out of the confirm without passing', async () => {
    const user = userEvent.setup();
    const p = setup({ selectedCount: 3 });
    await user.click(screen.getByRole('button', { name: 'Pass 3' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(p.onPass).not.toHaveBeenCalled();
    // Back to the action row.
    expect(screen.getByRole('button', { name: 'Pass 3' })).toBeInTheDocument();
  });

  test('the chosen reason is passed through on confirm', async () => {
    const user = userEvent.setup();
    const p = setup({ selectedCount: 2 });
    await user.selectOptions(screen.getByLabelText(/pass reason/i), 'too_senior');
    await user.click(screen.getByRole('button', { name: 'Pass 2' }));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(p.onPass).toHaveBeenCalledWith('too_senior');
  });

  test('Reset and Clear fire directly', async () => {
    const user = userEvent.setup();
    const p = setup({ selectedCount: 4 });
    await user.click(screen.getByRole('button', { name: 'Reset 4' }));
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(p.onReset).toHaveBeenCalledTimes(1);
    expect(p.onClear).toHaveBeenCalledTimes(1);
  });

  test('busy disables the action controls', () => {
    setup({ selectedCount: 4, busy: true });
    expect(screen.getByRole('button', { name: 'Pass 4' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset 4' })).toBeDisabled();
  });
});
