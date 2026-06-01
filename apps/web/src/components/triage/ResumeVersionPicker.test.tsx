import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { ResumeVersionPicker } from '@/components/triage/ResumeVersionPicker';

// Mock the data hook so the picker renders a fixed version list without
// a QueryClient / network. The component contract under test is:
// "the chosen version's id is passed to onSelect; skip calls onSkip."
vi.mock('@/lib/api/resume', () => ({
  useResumeVersions: () => ({
    data: {
      total: 2,
      items: [
        {
          id: 'rv-1',
          label: 'betterment-trust-v1',
          angle: 'trust',
          snapshot_text: null,
          notes: null,
          created_at: '',
        },
        {
          id: 'rv-2',
          label: 'generic-v2',
          angle: null,
          snapshot_text: null,
          notes: null,
          created_at: '',
        },
      ],
    },
    isLoading: false,
  }),
}));

describe('ResumeVersionPicker', () => {
  test('renders the version list from the hook', () => {
    render(<ResumeVersionPicker onSelect={() => {}} onSkip={() => {}} />);
    expect(screen.getByText('betterment-trust-v1')).toBeInTheDocument();
    expect(screen.getByText('generic-v2')).toBeInTheDocument();
    expect(screen.getByText('Skip')).toBeInTheDocument();
  });

  test('clicking a version passes its id to onSelect', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<ResumeVersionPicker onSelect={onSelect} onSkip={() => {}} />);
    await user.click(screen.getByText('betterment-trust-v1'));
    expect(onSelect).toHaveBeenCalledWith('rv-1');
  });

  test('clicking Skip calls onSkip (apply untagged)', async () => {
    const user = userEvent.setup();
    const onSkip = vi.fn();
    render(<ResumeVersionPicker onSelect={() => {}} onSkip={onSkip} />);
    await user.click(screen.getByText('Skip'));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  test('hotkey 2 selects the second version; Esc skips', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onSkip = vi.fn();
    render(<ResumeVersionPicker onSelect={onSelect} onSkip={onSkip} />);
    await user.keyboard('2');
    expect(onSelect).toHaveBeenCalledWith('rv-2');
    await user.keyboard('{Escape}');
    expect(onSkip).toHaveBeenCalledTimes(1);
  });
});
