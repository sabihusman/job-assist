import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { AppliedSortStrip } from '@/components/applied/AppliedSortStrip';

const replaceMock = vi.fn();
let currentParams = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => new URLSearchParams(currentParams),
}));

beforeEach(() => {
  replaceMock.mockClear();
});

describe('AppliedSortStrip', () => {
  test('default sort is "applied" with that pill active', () => {
    currentParams = '';
    render(<AppliedSortStrip />);
    expect(screen.getByRole('button', { name: 'applied' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
  });

  test('clicking "stage" writes ?sort=stage', async () => {
    currentParams = '';
    const user = userEvent.setup();
    render(<AppliedSortStrip />);
    await user.click(screen.getByRole('button', { name: 'stage' }));
    expect(replaceMock).toHaveBeenCalled();
    expect(replaceMock.mock.calls[0]?.[0]).toContain('sort=stage');
  });

  test('clicking "applied" while another sort active removes the param', async () => {
    currentParams = 'sort=tier';
    const user = userEvent.setup();
    render(<AppliedSortStrip />);
    await user.click(screen.getByRole('button', { name: 'applied' }));
    const url = replaceMock.mock.calls[0]?.[0] as string;
    expect(url).not.toContain('sort=');
  });
});
