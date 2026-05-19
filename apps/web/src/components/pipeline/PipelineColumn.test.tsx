import { render, screen, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { PipelineColumn } from '@/components/pipeline/PipelineColumn';
import type { ApplicationCard } from '@/lib/pipeline/bucket';

function card(id: string, name = 'Co'): ApplicationCard {
  return {
    id,
    tier: 1,
    companyName: name,
    roleTitle: 'PM',
    roleFamily: 'product_management',
    appliedAt: new Date().toISOString(),
  };
}

describe('PipelineColumn', () => {
  test('renders the stage label and count pill', () => {
    render(<PipelineColumn stage="applied" cards={[card('a'), card('b')]} />);
    expect(screen.getByText('APPLIED')).toBeInTheDocument();
    expect(screen.getByLabelText('2 cards')).toBeInTheDocument();
  });

  test('renders one li per card', () => {
    render(<PipelineColumn stage="applied" cards={[card('a'), card('b')]} />);
    const section = screen.getByRole('region', { name: /applied/i });
    expect(within(section).getAllByRole('listitem')).toHaveLength(2);
  });

  test('renders em-dash when empty', () => {
    render(<PipelineColumn stage="ghosted" cards={[]} />);
    expect(screen.getByTestId('pipeline-empty-ghosted')).toHaveTextContent('—');
  });
});
