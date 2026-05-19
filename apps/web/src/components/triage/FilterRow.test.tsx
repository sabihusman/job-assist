import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { FilterRow } from '@/components/triage/FilterRow';

const replaceMock = vi.fn();
let currentParams = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => new URLSearchParams(currentParams),
}));

function setParams(s: string) {
  currentParams = s;
}

describe('FilterRow', () => {
  test('clicking a TIER chip writes the tier param', async () => {
    setParams('');
    replaceMock.mockClear();
    const user = userEvent.setup();
    render(<FilterRow showing={0} total={0} />);
    await user.click(screen.getByRole('button', { name: 'T1' }));
    expect(replaceMock).toHaveBeenCalled();
    const url = replaceMock.mock.calls[0]?.[0] as string;
    expect(url).toContain('tier=1');
    expect(url).toContain('state=triage');
  });

  test('clicking a selected TIER chip removes it', async () => {
    setParams('tier=1&state=triage');
    replaceMock.mockClear();
    const user = userEvent.setup();
    render(<FilterRow showing={0} total={0} />);
    // T1 should be selected.
    expect(screen.getByRole('button', { name: 'T1' }).getAttribute('aria-pressed')).toBe('true');
    await user.click(screen.getByRole('button', { name: 'T1' }));
    const url = replaceMock.mock.calls[0]?.[0] as string;
    expect(url).not.toContain('tier=1');
  });

  test('multi-select within a group: T1 then T2 yields tier=1&tier=2', async () => {
    setParams('tier=1');
    replaceMock.mockClear();
    const user = userEvent.setup();
    render(<FilterRow showing={0} total={0} />);
    await user.click(screen.getByRole('button', { name: 'T2' }));
    const url = replaceMock.mock.calls[0]?.[0] as string;
    expect(url).toContain('tier=1');
    expect(url).toContain('tier=2');
  });

  test('renders the showing N of M label', () => {
    setParams('');
    render(<FilterRow showing={20} total={42} />);
    expect(screen.getByText(/showing 20 of 42/)).toBeInTheDocument();
  });
});
