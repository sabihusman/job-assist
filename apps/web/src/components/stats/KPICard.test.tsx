import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { KPICard } from '@/components/stats/KPICard';

describe('KPICard', () => {
  test('renders label, value, delta, and caption', () => {
    render(<KPICard label="Test KPI" value="42" delta={4} caption="caption text" />);
    expect(screen.getByText('Test KPI')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('+4')).toBeInTheDocument();
    expect(screen.getByText('caption text')).toBeInTheDocument();
  });

  test('omits delta when null/undefined', () => {
    const { container } = render(<KPICard label="Test" value="—" />);
    // No element should contain a sign prefix.
    expect(container.textContent).not.toMatch(/\+/);
  });

  test('negative delta uses minus prefix from JS implicit', () => {
    render(<KPICard label="Test" value="100" delta={-12} />);
    expect(screen.getByText('-12')).toBeInTheDocument();
  });
});
