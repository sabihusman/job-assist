import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { RepeatSignalBadges } from '@/components/shared/RepeatSignalBadges';
import type { RepeatSignals } from '@/lib/api/companySignals';

const SIGNALS: RepeatSignals = {
  rejectCo: { rejections: 3, active_apps: 1 },
  aliveCo: { rejections: 0, active_apps: 2 },
  bothCo: { rejections: 4, active_apps: 3 },
  onceCo: { rejections: 1, active_apps: 1 },
};

describe('RepeatSignalBadges', () => {
  test('renders nothing for a company below threshold', () => {
    const { container } = render(<RepeatSignalBadges companyId="onceCo" signals={SIGNALS} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders nothing for an unknown company', () => {
    const { container } = render(<RepeatSignalBadges companyId="missing" signals={SIGNALS} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders nothing when companyId is null', () => {
    const { container } = render(<RepeatSignalBadges companyId={null} signals={SIGNALS} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders nothing when signals are undefined (not yet loaded)', () => {
    const { container } = render(<RepeatSignalBadges companyId="rejectCo" signals={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('shows only the rejections badge when active < 2', () => {
    render(<RepeatSignalBadges companyId="rejectCo" signals={SIGNALS} />);
    expect(screen.getByText('3 rejections here')).toBeInTheDocument();
    expect(screen.queryByText(/active apps/)).not.toBeInTheDocument();
  });

  test('shows only the active-apps badge when rejections < 2', () => {
    render(<RepeatSignalBadges companyId="aliveCo" signals={SIGNALS} />);
    expect(screen.getByText('2 active apps here')).toBeInTheDocument();
    expect(screen.queryByText(/rejections/)).not.toBeInTheDocument();
  });

  test('shows both badges when both cross the threshold', () => {
    render(<RepeatSignalBadges companyId="bothCo" signals={SIGNALS} />);
    expect(screen.getByText('4 rejections here')).toBeInTheDocument();
    expect(screen.getByText('3 active apps here')).toBeInTheDocument();
  });
});
