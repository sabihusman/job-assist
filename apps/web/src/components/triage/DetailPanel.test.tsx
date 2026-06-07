import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { DetailPanel } from '@/components/triage/DetailPanel';
import type { PostingDetail } from '@/lib/triage/types';

const mockState = vi.hoisted(() => ({
  data: null as PostingDetail | null,
  isLoading: false,
}));

vi.mock('@/lib/api/hooks', () => ({
  usePosting: () => ({ data: mockState.data, isLoading: mockState.isLoading }),
  queryKeys: { posting: (id: string) => ['posting', id] },
}));

function makeDetail(overrides: Partial<PostingDetail> = {}): PostingDetail {
  return {
    id: 'p-detail-1',
    company: {
      id: 'c-1',
      name: 'DetailCo',
      domain: null,
      description: 'DetailCo description.',
      tier: 1,
    },
    role: {
      title: 'Detail Role',
      family: 'product_management',
      department: 'Product',
      team: null,
      seniority: 'senior_pm',
    },
    location_raw: 'San Francisco, CA',
    locations_normalized: ['San Francisco, CA'],
    remote_type: 'hybrid',
    salary: { min: 200000, max: 250000, currency: 'USD', period: 'annual' },
    source: { ats: 'ashby', url: 'https://example.test/jd' },
    first_seen_at: new Date().toISOString(),
    score: null,
    state: { current: null, reason: null, snooze_until: null, current_at: null },
    description_markdown: '## About the role\n\n- bullet one\n- bullet two',
    jd_summary_markdown: null,
    division: null,
    posted_at: null,
    last_seen_at: null,
    closed_at: null,
    state_history: [],
    resume: null,
    gmail_outcome: null,
    ...overrides,
  };
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

/**
 * Scope queries to the desktop aside. PR 1 UX overhaul: DetailPanel
 * now renders BOTH a desktop aside AND a mobile Sheet portal when a
 * posting is selected. Both DOM trees contain the same body content,
 * so unscoped queries find duplicates. Tests that don't care about
 * which surface (i.e. anything except the empty state) should scope
 * via this helper.
 */
// Radix Dialog (used internally by Sheet) marks everything outside
// the dialog as ``aria-hidden="true"`` when open, including our
// desktop aside. ``hidden: true`` re-includes aria-hidden elements
// in the role match. Both surfaces render the same content; we pick
// the aside because it's the source-of-truth desktop layout.
const panel = () =>
  within(screen.getByRole('complementary', { name: 'Posting details', hidden: true }));

afterEach(() => {
  mockState.data = null;
  mockState.isLoading = false;
});

describe('DetailPanel', () => {
  test('renders the empty state when no posting is selected', () => {
    wrap(<DetailPanel selectedId={null} onClose={() => {}} onAction={() => {}} />);
    expect(screen.getByText(/select a posting to see details/i)).toBeInTheDocument();
  });

  // ── PR 2: desktop aside is sticky-positioned ────────────────────────────
  //
  // Pre-PR-2 the aside scrolled with the page because it was a sibling
  // flex child without ``position: sticky``. Clicking a card lower in
  // the list rendered the panel above the viewport. This regression
  // lock asserts the sticky + top offset stays on the aside — losing
  // either silently re-introduces the operator-facing friction.
  test('desktop aside has sticky top-12 classes (PR 2 sticky detail panel)', () => {
    wrap(<DetailPanel selectedId={null} onClose={() => {}} onAction={() => {}} />);
    // <aside> exposes as role "complementary", not "region", when it
    // has aria-label. The panel() helper used elsewhere in this file
    // works around this with hidden:true; the regression we're testing
    // here is the class string itself, so query by name attribute.
    const aside = screen.getByLabelText('Posting details');
    const cls = aside.getAttribute('class') ?? '';
    expect(cls).toContain('sticky');
    // Banner is h-12; the aside's top offset must match so it pins
    // directly below the banner.
    expect(cls).toContain('top-12');
    // ``self-start`` keeps the sticky calc honest in a flex parent —
    // without it the aside stretches to the row height of the longest
    // sibling and sticky has nothing to stick against.
    expect(cls).toContain('self-start');
  });

  test('renders the division-pending callout when division is null but the role has a department', () => {
    // Default fixture has role.department='Product' → genuinely awaiting the sweep.
    mockState.data = makeDetail({ division: null });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(panel().getByText(/division info pending/i)).toBeInTheDocument();
  });

  test('shows "no business division" (NOT pending) when the role has no department or team', () => {
    // Apify-sourced roles (and any ATS that doesn't surface a department) have
    // department=null AND team=null → division discovery can never run on them,
    // so the "will populate" promise must NOT show.
    mockState.data = makeDetail({
      division: null,
      role: {
        title: 'Apify Role',
        family: 'product_management',
        department: null,
        team: null,
        seniority: 'pm',
      },
    });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(panel().getByText(/no business division for this role/i)).toBeInTheDocument();
    expect(panel().queryByText(/division info pending/i)).not.toBeInTheDocument();
  });

  test('renders the markdown JD', () => {
    mockState.data = makeDetail();
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(panel().getByRole('heading', { level: 2, name: /about the role/i, hidden: true }));
    expect(panel().getByText(/bullet one/)).toBeInTheDocument();
  });

  test('Open JD anchor targets a new tab', () => {
    mockState.data = makeDetail();
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    const anchor = panel().getByRole('link', {
      name: /open job description in new tab/i,
      hidden: true,
    });
    expect(anchor.getAttribute('target')).toBe('_blank');
    expect(anchor.getAttribute('href')).toBe('https://example.test/jd');
  });

  // ── PR #42: jd_summary_markdown surfacing ────────────────────────────────

  test('renders summary when jd_summary_markdown is present', () => {
    mockState.data = makeDetail({
      jd_summary_markdown: '**Scope**: Senior PM owns fraud signals.\n\n**Comp**: $200k-$250k.',
    });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(panel().getByText(/job description \(summary\)/i)).toBeInTheDocument();
    expect(panel().getByText(/Senior PM owns fraud signals/i)).toBeInTheDocument();
    // Toggle is offered, but the full description is NOT yet rendered.
    expect(
      panel().getByRole('button', { name: /show full description/i, hidden: true }),
    ).toBeInTheDocument();
    expect(panel().queryByText(/bullet one/)).not.toBeInTheDocument();
  });

  test('renders full JD when jd_summary_markdown is null', () => {
    mockState.data = makeDetail({ jd_summary_markdown: null });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    // Plain heading (no "(summary)" suffix) — and the JD body is visible
    // immediately without any toggle.
    expect(
      panel().getByRole('heading', { level: 4, name: /^job description$/i, hidden: true }),
    ).toBeInTheDocument();
    expect(panel().getByText(/bullet one/)).toBeInTheDocument();
    expect(
      panel().queryByRole('button', { name: /show full description/i, hidden: true }),
    ).not.toBeInTheDocument();
  });

  test('shows pending footnote when summary is null but full JD exists', () => {
    mockState.data = makeDetail({ jd_summary_markdown: null });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(panel().getByText(/summary pending/i)).toBeInTheDocument();
  });

  test('toggle expands the full JD below the summary', () => {
    mockState.data = makeDetail({
      jd_summary_markdown: '**Scope**: short summary line.',
    });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    // Start collapsed.
    expect(panel().queryByText(/bullet one/)).not.toBeInTheDocument();
    fireEvent.click(panel().getByRole('button', { name: /show full description/i, hidden: true }));
    expect(panel().getByText(/bullet one/)).toBeInTheDocument();
    expect(
      panel().getByRole('button', { name: /hide full description/i, hidden: true }),
    ).toBeInTheDocument();
  });

  test('toggle collapses the full JD again on second click', () => {
    mockState.data = makeDetail({
      jd_summary_markdown: '**Scope**: short summary line.',
    });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    const btn = panel().getByRole('button', { name: /show full description/i, hidden: true });
    fireEvent.click(btn);
    expect(panel().getByText(/bullet one/)).toBeInTheDocument();
    fireEvent.click(panel().getByRole('button', { name: /hide full description/i, hidden: true }));
    expect(panel().queryByText(/bullet one/)).not.toBeInTheDocument();
  });

  // ── PR #76: Score field reads from posting.score (not hardcoded —) ──────

  test('Score field renders the numeric score from posting.score', () => {
    // Pre-PR-#76 the value was hardcoded ``"—"`` regardless of the
    // posting payload. This regression-locks the wiring: the panel
    // MUST read from posting.score, otherwise the silent placeholder
    // returns.
    mockState.data = makeDetail({ score: 91 });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);

    // Find the dt labeled "Score" inside the desktop aside, read its
    // sibling dd.
    const scoreLabel = panel().getByText('Score');
    const scoreValue = scoreLabel.nextElementSibling;
    expect(scoreValue?.textContent).toBe('91');
  });

  test('Score field renders em-dash when posting.score is null', () => {
    mockState.data = makeDetail({ score: null });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);

    const scoreLabel = panel().getByText('Score');
    const scoreValue = scoreLabel.nextElementSibling;
    expect(scoreValue?.textContent).toBe('—');
  });

  test('toggle state resets when the selected posting id changes', () => {
    // Open the toggle on posting 1, then re-render with posting 2 selected.
    // The component is keyed on posting.id so showFullJd resets to false.
    mockState.data = makeDetail({
      id: 'p-1',
      jd_summary_markdown: '**Scope**: posting 1.',
    });
    const { rerender } = wrap(
      <DetailPanel selectedId={'p-1'} onClose={() => {}} onAction={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /show full description/i }));
    expect(screen.getByText(/bullet one/)).toBeInTheDocument();

    // Switch to a different posting.
    mockState.data = makeDetail({
      id: 'p-2',
      jd_summary_markdown: '**Scope**: posting 2.',
    });
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <DetailPanel selectedId={'p-2'} onClose={() => {}} onAction={() => {}} />
      </QueryClientProvider>,
    );

    // Toggle should be back to "Show full description" — full text hidden.
    expect(screen.getByRole('button', { name: /show full description/i })).toBeInTheDocument();
    expect(screen.queryByText(/bullet one/)).not.toBeInTheDocument();
  });

  // ── feat/applied-pipeline-crosslink: read-only Gmail Pipeline pointer ──────

  test('renders the Gmail Pipeline cross-link chip when gmail_outcome is present', () => {
    mockState.data = makeDetail({
      gmail_outcome: {
        outcome_event_id: 'oe-1',
        stage: 'application_confirmation',
        received_at: '2026-04-14T00:00:00Z',
        email_thread_id: 't-1',
        subject: 'Thanks for applying',
      },
    });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    const link = panel().getByTestId('gmail-outcome-link');
    expect(link).toBeInTheDocument();
    // Navigational only — links to the Pipeline, never mutates state.
    expect(link.getAttribute('href')).toBe('/pipeline');
    expect(link.textContent).toMatch(/gmail/i);
  });

  test('no Gmail cross-link chip when gmail_outcome is null', () => {
    mockState.data = makeDetail({ gmail_outcome: null });
    wrap(<DetailPanel selectedId={'p-detail-1'} onClose={() => {}} onAction={() => {}} />);
    expect(panel().queryByTestId('gmail-outcome-link')).not.toBeInTheDocument();
  });
});
