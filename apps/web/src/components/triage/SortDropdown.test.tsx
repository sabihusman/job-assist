import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { SortDropdown } from '@/components/triage/SortDropdown';

describe('SortDropdown', () => {
  test('renders all 6 options with their operator-facing labels', () => {
    render(<SortDropdown value="newest" onChange={() => {}} />);
    const select = screen.getByRole('combobox', { name: /sort/i });
    const options = Array.from(select.querySelectorAll('option')).map((o) => ({
      value: o.value,
      text: o.textContent,
    }));
    expect(options).toEqual([
      { value: 'newest', text: 'Newest' },
      { value: 'oldest', text: 'Oldest' },
      { value: 'salary_high_to_low', text: 'Salary high to low' },
      { value: 'tier', text: 'Tier' },
      { value: 'recently_posted', text: 'Recently posted' },
      // PR #57: "Best fit" reads fit_score DESC NULLS LAST.
      { value: 'best_fit', text: 'Best fit' },
    ]);
  });

  test('PR #57: best_fit option is selectable and fires onChange', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<SortDropdown value="newest" onChange={onChange} />);
    const select = screen.getByRole('combobox', { name: /sort/i });
    await user.selectOptions(select, 'best_fit');
    expect(onChange).toHaveBeenCalledWith('best_fit');
  });

  test('selected value reflects the prop', () => {
    render(<SortDropdown value="tier" onChange={() => {}} />);
    const select = screen.getByRole('combobox', { name: /sort/i }) as HTMLSelectElement;
    expect(select.value).toBe('tier');
  });

  test('changing the select fires onChange with the new wire key', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<SortDropdown value="newest" onChange={onChange} />);
    const select = screen.getByRole('combobox', { name: /sort/i });
    await user.selectOptions(select, 'salary_high_to_low');
    expect(onChange).toHaveBeenCalledWith('salary_high_to_low');
  });

  test('accessible by label association', () => {
    render(<SortDropdown value="newest" onChange={() => {}} />);
    // getByRole with `name` only finds it if the <label> for= htmlFor= wiring
    // is correct — useId() generates a unique id that we point both at.
    expect(screen.getByRole('combobox', { name: /sort/i })).toBeInTheDocument();
  });
});
