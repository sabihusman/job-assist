import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { ConfirmRulesModal, type RuleChange } from '@/components/settings/ConfirmRulesModal';

const ONE_CHANGE: RuleChange[] = [{ label: 'Salary floor', from: '$85K', to: '$90K' }];
const TWO_CHANGES: RuleChange[] = [
  ...ONE_CHANGE,
  { label: 'Roles per company', from: '3', to: '5' },
];

describe('ConfirmRulesModal', () => {
  test('heading uses singular for one change, plural for many', () => {
    const onOpen = vi.fn();
    const onSave = vi.fn();
    const { rerender } = render(
      <ConfirmRulesModal
        open={true}
        onOpenChange={onOpen}
        changes={ONE_CHANGE}
        onSave={onSave}
        isSaving={false}
        error={null}
      />,
    );
    expect(screen.getByText('Save 1 rule change?')).toBeInTheDocument();
    rerender(
      <ConfirmRulesModal
        open={true}
        onOpenChange={onOpen}
        changes={TWO_CHANGES}
        onSave={onSave}
        isSaving={false}
        error={null}
      />,
    );
    expect(screen.getByText('Save 2 rule changes?')).toBeInTheDocument();
  });

  test('renders each change as {label}: {from} → {to}', () => {
    render(
      <ConfirmRulesModal
        open={true}
        onOpenChange={() => {}}
        changes={TWO_CHANGES}
        onSave={() => {}}
        isSaving={false}
        error={null}
      />,
    );
    expect(screen.getByText('Salary floor')).toBeInTheDocument();
    expect(screen.getByText('$85K')).toBeInTheDocument();
    expect(screen.getByText('$90K')).toBeInTheDocument();
    expect(screen.getByText('Roles per company')).toBeInTheDocument();
  });

  test('Cancel button calls onOpenChange(false)', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <ConfirmRulesModal
        open={true}
        onOpenChange={onOpen}
        changes={ONE_CHANGE}
        onSave={() => {}}
        isSaving={false}
        error={null}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpen).toHaveBeenCalledWith(false);
  });

  test('Save changes button calls onSave', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <ConfirmRulesModal
        open={true}
        onOpenChange={() => {}}
        changes={ONE_CHANGE}
        onSave={onSave}
        isSaving={false}
        error={null}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalled();
  });

  test('Save changes is disabled when changes is empty', () => {
    render(
      <ConfirmRulesModal
        open={true}
        onOpenChange={() => {}}
        changes={[]}
        onSave={() => {}}
        isSaving={false}
        error={null}
      />,
    );
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  });

  test('renders inline error message', () => {
    render(
      <ConfirmRulesModal
        open={true}
        onOpenChange={() => {}}
        changes={ONE_CHANGE}
        onSave={() => {}}
        isSaving={false}
        error="DB unreachable"
      />,
    );
    expect(screen.getByText('DB unreachable')).toBeInTheDocument();
  });
});
