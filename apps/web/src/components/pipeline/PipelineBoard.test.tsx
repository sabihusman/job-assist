import { render, screen, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { PipelineBoard } from '@/components/pipeline/PipelineBoard';
import type { PipelineStage } from '@/lib/applied/stages';
import { type ApplicationCard, emptyBuckets } from '@/lib/pipeline/bucket';

function card(id: string, name: string, appliedAt = '2026-05-01T00:00:00Z'): ApplicationCard {
  return {
    id,
    companyName: name,
    roleTitle: 's',
    roleFamily: null,
    appliedAt,
  };
}

function buckets() {
  const b = emptyBuckets();
  b.rejected.push(card('r1', 'RejCo'));
  b.applied.push(card('a1', 'AppCo'));
  return b;
}

function stageSequence(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[data-stage]')).map((s) =>
    s.getAttribute('data-stage'),
  ) as string[];
}

describe('PipelineBoard reorder is presentational only', () => {
  test('renders columns in the provided order', () => {
    // The real board order (feat/still-alive: no ghosted column).
    const order: PipelineStage[] = [
      'rejected',
      'applied',
      'recruiter',
      'phone',
      'video',
      'onsite',
      'offer',
    ];
    const { container } = render(<PipelineBoard buckets={buckets()} order={order} />);
    expect(stageSequence(container)).toEqual(order);
    expect(stageSequence(container)).not.toContain('ghosted');
  });

  test('reordering changes only render order, NOT which column a card is in', () => {
    const a = render(
      <PipelineBoard buckets={buckets()} order={['applied', 'rejected'] as never} />,
    );
    // card stays in its stage column regardless of position
    expect(
      within(screen.getByRole('region', { name: /rejected/i })).getByText('RejCo'),
    ).toBeInTheDocument();
    a.unmount();

    const b = render(
      <PipelineBoard buckets={buckets()} order={['rejected', 'applied'] as never} />,
    );
    // different column order, but RejCo is STILL in the rejected column
    expect(
      within(screen.getByRole('region', { name: /rejected/i })).getByText('RejCo'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('region', { name: /still alive/i })).getByText('AppCo'),
    ).toBeInTheDocument();
    b.unmount();
  });
});

describe('PipelineBoard per-column sort by date', () => {
  function dated(): ReturnType<typeof emptyBuckets> {
    const b = emptyBuckets();
    // Out-of-order on purpose so the sort has to do work.
    b.applied.push(card('mid', 'Mid', '2026-05-15T00:00:00Z'));
    b.applied.push(card('new', 'New', '2026-06-01T00:00:00Z'));
    b.applied.push(card('old', 'Old', '2026-04-01T00:00:00Z'));
    return b;
  }

  function cardOrder(): string[] {
    const col = screen.getByRole('region', { name: /still alive/i });
    return Array.from(col.querySelectorAll('[data-card-id]')).map(
      (el) => el.getAttribute('data-card-id') ?? '',
    );
  }

  test("'recent' (default) orders newest-first within a column", () => {
    render(<PipelineBoard buckets={dated()} sort="recent" />);
    expect(cardOrder()).toEqual(['new', 'mid', 'old']);
  });

  test("'oldest' reverses to oldest-first", () => {
    render(<PipelineBoard buckets={dated()} sort="oldest" />);
    expect(cardOrder()).toEqual(['old', 'mid', 'new']);
  });

  test('default sort (no prop) is most-recent-first', () => {
    render(<PipelineBoard buckets={dated()} />);
    expect(cardOrder()).toEqual(['new', 'mid', 'old']);
  });
});
