import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, test } from 'vitest';

import { TagInput } from '@/components/shared/TagInput';

function Harness({ initial = [] as string[] }: { initial?: string[] }) {
  const [value, setValue] = useState<string[]>(initial);
  return <TagInput value={value} onChange={setValue} placeholder="add…" inputAriaLabel="add tag" />;
}

describe('TagInput', () => {
  test('Enter commits a chip', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText(/add tag/i);
    await user.type(input, 'foo{Enter}');
    expect(screen.getByText('foo')).toBeInTheDocument();
  });

  test('comma commits a chip', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText(/add tag/i);
    await user.type(input, 'bar,');
    expect(screen.getByText('bar')).toBeInTheDocument();
  });

  test('× button removes a chip', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['alpha', 'beta']} />);
    await user.click(screen.getByLabelText('Remove alpha'));
    expect(screen.queryByText('alpha')).not.toBeInTheDocument();
    expect(screen.getByText('beta')).toBeInTheDocument();
  });

  test('dedupes on commit', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['foo']} />);
    const input = screen.getByLabelText(/add tag/i);
    await user.type(input, 'foo{Enter}');
    // Only one chip with text foo.
    expect(screen.getAllByText('foo')).toHaveLength(1);
  });

  test('trims whitespace before commit', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText(/add tag/i);
    await user.type(input, '   spacey   {Enter}');
    expect(screen.getByText('spacey')).toBeInTheDocument();
  });

  test('Backspace on empty input removes last chip', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['a', 'b']} />);
    const input = screen.getByLabelText(/add tag/i);
    input.focus();
    await user.keyboard('{Backspace}');
    expect(screen.queryByText('b')).not.toBeInTheDocument();
    expect(screen.getByText('a')).toBeInTheDocument();
  });
});
