import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

import type { OutcomeEvent } from '@/lib/applied/types';

// Desktop so the in-place aside renders (Sheet stays closed).
vi.mock('@/lib/use-media-query', () => ({ useIsLgUp: () => true }));

// Mutable holder so each test can set the cached outcomes the panel reads.
const holder = vi.hoisted(() => ({ items: [] as unknown[] }));
vi.mock('@/lib/api/applied', () => ({
  useAllOutcomes: () => ({ data: holder, isLoading: false }),
}));

import { PipelineDetailPanel } from '@/components/pipeline/PipelineDetailPanel';

function oc(p: Partial<OutcomeEvent> & { id: string; stage: string }): OutcomeEvent {
  return {
    id: p.id,
    posting_id: null,
    target_company_id: null,
    received_at: p.received_at ?? '2026-05-01T00:00:00Z',
    stage: p.stage,
    confidence: 0.9,
    company_name: p.company_name ?? null,
    subject: p.subject ?? 'Thank you for applying to Acme',
    from_domain: p.from_domain ?? 'greenhouse.io',
    email_thread_id: p.email_thread_id ?? null,
    raw_snippet: p.raw_snippet ?? null,
  };
}

describe('PipelineDetailPanel', () => {
  beforeEach(() => {
    holder.items = [];
  });

  test('renders subject + snippet + chronological timeline for a multi-event thread', () => {
    holder.items = [
      oc({
        id: 'e1',
        stage: 'application_confirmation',
        email_thread_id: 't-1',
        received_at: '2026-05-01T00:00:00Z',
        subject: 'Thank you for applying to Ramp - Senior Product Manager',
        raw_snippet: 'Thanks for applying to Ramp. We received your application.',
      }),
      oc({
        id: 'e2',
        stage: 'rejection_post_screen',
        email_thread_id: 't-1',
        received_at: '2026-05-10T00:00:00Z',
        subject: 'Update on your Ramp application',
        raw_snippet: 'We have decided not to move forward.',
      }),
    ];
    render(<PipelineDetailPanel selectedId="t:t-1" onClose={() => {}} />);
    const aside = screen.getByRole('complementary', { name: /application details/i });

    // company label (from subject extraction), both subjects, both snippets.
    expect(within(aside).getAllByText('Ramp').length).toBeGreaterThan(0);
    expect(
      within(aside).getByText('Thank you for applying to Ramp - Senior Product Manager'),
    ).toBeInTheDocument();
    expect(within(aside).getByText('Update on your Ramp application')).toBeInTheDocument();
    expect(
      within(aside).getByText('Thanks for applying to Ramp. We received your application.'),
    ).toBeInTheDocument();
    expect(within(aside).getByText('We have decided not to move forward.')).toBeInTheDocument();

    // Two timeline items, chronological (confirmation first, rejection second).
    const items = within(aside).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Thank you for applying to Ramp');
    expect(items[1]).toHaveTextContent('Update on your Ramp application');
  });

  test('shows the role when extractable from the subject', () => {
    holder.items = [
      oc({
        id: 'e1',
        stage: 'application_confirmation',
        email_thread_id: 't-2',
        subject: 'Covr Financial Technologies - Jr. Product Manager',
      }),
    ];
    render(<PipelineDetailPanel selectedId="t:t-2" onClose={() => {}} />);
    expect(screen.getByTestId('detail-role')).toHaveTextContent('Jr. Product Manager');
  });

  test('OMITS the role when the subject has none (does not promise it)', () => {
    holder.items = [
      oc({
        id: 'e1',
        stage: 'application_confirmation',
        email_thread_id: 't-3',
        subject: 'Thank you for applying to Goldman Sachs',
      }),
    ];
    render(<PipelineDetailPanel selectedId="t:t-3" onClose={() => {}} />);
    expect(screen.queryByTestId('detail-role')).not.toBeInTheDocument();
  });

  test('empty body when nothing is selected', () => {
    render(<PipelineDetailPanel selectedId={null} onClose={() => {}} />);
    expect(screen.getByText(/select a card/i)).toBeInTheDocument();
  });
});
