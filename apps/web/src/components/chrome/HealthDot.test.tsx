import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';

import { HealthDotView } from '@/components/chrome/HealthDot';
import type { IngestHealth } from '@/lib/api/health';

function makeHealth(over: Partial<IngestHealth> = {}): IngestHealth {
  return {
    ok: true,
    severity: 'ok',
    problems: [],
    checks: {
      curated_fresh: true,
      no_hard_failures: true,
      broad_fresh: true,
      not_starved: true,
      llm_healthy: true,
      gmail_healthy: true,
      warm_path_fresh: true,
      wellfound_fresh: true,
    },
    metrics: {
      last_success_at: '2026-06-07T22:00:00Z',
      failed_runs_recent: 0,
      handle_not_found_recent: 0,
      curated_companies: 30,
      curated_last_swept_at: '2026-06-07T22:00:00Z',
      broad_last_swept_at: '2026-06-07T21:00:00Z',
      broad_qualified_this_week: 12,
      broad_weekly_cap: 100,
      broad_cap_met: false,
      reclassify_pending: 0,
      net_new_starvation_window: 12,
      window_hours: 26,
      starvation_days: 3,
      llm_last_used_at: '2026-06-07T21:30:00Z',
      llm_last_classified_at: '2026-06-07T21:30:00Z',
      llm_last_embedded_at: '2026-06-07T20:00:00Z',
      llm_exhausted_errors: 0,
      llm_stale_hours: 24,
      gmail_last_sweep_at: '2026-06-07T21:45:00Z',
      gmail_last_sweep_status: 'success',
      gmail_last_sweep_runtime_seconds: 12.4,
      gmail_stale_hours: 13,
      warm_path_companies: 0,
      warm_path_last_swept_at: null,
      warm_path_stale_days: 9,
      wellfound_companies: 0,
      wellfound_last_swept_at: null,
      wellfound_stale_days: 3,
    },
    ...over,
  };
}

describe('HealthDotView', () => {
  test('GREEN: ok state → emerald dot, all checks pass', async () => {
    render(<HealthDotView state="ok" health={makeHealth()} isError={false} />);
    const dot = screen.getByTestId('health-dot');
    expect(dot).toHaveAttribute('data-state', 'ok');
    expect(dot.className).toContain('bg-emerald-500');
    expect(dot).toHaveAccessibleName(/healthy/i);

    await userEvent.click(dot);
    expect(screen.getByTestId('health-popover')).toBeInTheDocument();
    expect(screen.getByTestId('health-check-curated_fresh')).toHaveAttribute('data-pass', 'true');
    expect(screen.getByTestId('health-check-not_starved')).toHaveAttribute('data-pass', 'true');
  });

  test('YELLOW: degraded state → amber dot, the soft check shows failing', async () => {
    const health = makeHealth({
      ok: false,
      severity: 'degraded',
      problems: ['starvation: only 0 net-new posting(s) in the last 3 days'],
      checks: {
        curated_fresh: true,
        no_hard_failures: true,
        broad_fresh: true,
        not_starved: false,
        llm_healthy: true,
        gmail_healthy: true,
        warm_path_fresh: true,
        wellfound_fresh: true,
      },
    });
    render(<HealthDotView state="degraded" health={health} isError={false} />);
    const dot = screen.getByTestId('health-dot');
    expect(dot).toHaveAttribute('data-state', 'degraded');
    expect(dot.className).toContain('bg-amber-500');

    await userEvent.click(dot);
    expect(screen.getByTestId('health-check-not_starved')).toHaveAttribute('data-pass', 'false');
    expect(screen.getByTestId('health-check-curated_fresh')).toHaveAttribute('data-pass', 'true');
  });

  test('RED: down state → red dot, hard check failing', async () => {
    const health = makeHealth({
      ok: false,
      severity: 'down',
      problems: ['1 failed ingest_run(s) in the last 26h'],
      checks: {
        curated_fresh: true,
        no_hard_failures: false,
        broad_fresh: true,
        not_starved: true,
        llm_healthy: true,
        gmail_healthy: true,
        warm_path_fresh: true,
        wellfound_fresh: true,
      },
    });
    render(<HealthDotView state="down" health={health} isError={false} />);
    const dot = screen.getByTestId('health-dot');
    expect(dot).toHaveAttribute('data-state', 'down');
    expect(dot.className).toContain('bg-red-500');

    await userEvent.click(dot);
    expect(screen.getByTestId('health-check-no_hard_failures')).toHaveAttribute(
      'data-pass',
      'false',
    );
  });

  test('GMAIL: a stalled Gmail sweep shows the gmail check failing (soft/yellow)', async () => {
    const health = makeHealth({
      ok: false,
      severity: 'degraded',
      problems: ['Gmail sweep has not run in the last 13h (last sweep: None)'],
      checks: {
        curated_fresh: true,
        no_hard_failures: true,
        broad_fresh: true,
        not_starved: true,
        llm_healthy: true,
        gmail_healthy: false,
        warm_path_fresh: true,
        wellfound_fresh: true,
      },
    });
    render(<HealthDotView state="degraded" health={health} isError={false} />);
    await userEvent.click(screen.getByTestId('health-dot'));
    expect(screen.getByTestId('health-check-gmail_healthy')).toHaveAttribute('data-pass', 'false');
  });

  test('WELLFOUND: a sustained Wellfound failure shows the check failing (soft/yellow)', async () => {
    const health = makeHealth({
      ok: false,
      severity: 'degraded',
      problems: ['Wellfound sweep has not succeeded in the last 3 days (2 wellfound companies)'],
      checks: {
        curated_fresh: true,
        no_hard_failures: true,
        broad_fresh: true,
        not_starved: true,
        llm_healthy: true,
        gmail_healthy: true,
        warm_path_fresh: true,
        wellfound_fresh: false,
      },
    });
    render(<HealthDotView state="degraded" health={health} isError={false} />);
    await userEvent.click(screen.getByTestId('health-dot'));
    const row = screen.getByTestId('health-check-wellfound_fresh');
    expect(row).toHaveAttribute('data-pass', 'false');
    expect(row).toHaveTextContent('Wellfound sweep fresh');
  });

  test('GMAIL: popover shows the last sweep time + runtime', async () => {
    render(<HealthDotView state="ok" health={makeHealth()} isError={false} />);
    await userEvent.click(screen.getByTestId('health-dot'));
    const line = screen.getByTestId('health-gmail-sweep');
    // 12.4s runtime renders as "ran 12.4s"; >=60s would format as "Xm YYs".
    expect(line).toHaveTextContent(/Gmail sweep:/);
    expect(line).toHaveTextContent(/ran 12\.4s/);
  });

  test('UNREACHABLE → RED (never green/unknown): isError shows red + unreachable note', async () => {
    // The backend is dead → no health data, isError=true. Must read red.
    render(<HealthDotView state="down" health={undefined} isError={true} />);
    const dot = screen.getByTestId('health-dot');
    expect(dot).toHaveAttribute('data-state', 'down');
    expect(dot.className).toContain('bg-red-500');

    await userEvent.click(dot);
    expect(screen.getByTestId('health-popover')).toHaveTextContent(/unreachable/i);
  });

  test('loading → neutral pulsing dot (not green), no false-healthy', async () => {
    render(<HealthDotView state="loading" health={undefined} isError={false} />);
    const dot = screen.getByTestId('health-dot');
    expect(dot).toHaveAttribute('data-state', 'loading');
    expect(dot.className).toContain('animate-pulse');
    // Critically, loading must NOT be emerald (no premature green).
    expect(dot.className).not.toContain('bg-emerald-500');

    await userEvent.click(dot);
    expect(screen.getByTestId('health-popover')).toHaveTextContent(/checking/i);
  });

  test('popover opens on hover and the dot is a labelled button (a11y)', async () => {
    render(<HealthDotView state="ok" health={makeHealth()} isError={false} />);
    expect(screen.queryByTestId('health-popover')).not.toBeInTheDocument();
    await userEvent.hover(screen.getByTestId('health-dot'));
    expect(screen.getByTestId('health-popover')).toBeInTheDocument();
  });

  test('LLM: popover shows the LLM check row + "LLM last used" timestamp', async () => {
    const health = makeHealth({
      ok: false,
      severity: 'degraded',
      problems: ['classifier sweep has not run in the last 24h'],
      checks: {
        curated_fresh: true,
        no_hard_failures: true,
        broad_fresh: true,
        not_starved: true,
        llm_healthy: false,
        gmail_healthy: true,
        warm_path_fresh: true,
        wellfound_fresh: true,
      },
    });
    render(<HealthDotView state="degraded" health={health} isError={false} />);
    await userEvent.click(screen.getByTestId('health-dot'));
    expect(screen.getByTestId('health-check-llm_healthy')).toHaveAttribute('data-pass', 'false');
    expect(screen.getByText(/LLM last used:/)).toBeInTheDocument();
  });
});
