import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { ExportCsvButton } from '@/components/shared/ExportCsvButton';

describe('ExportCsvButton', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('click builds the CSV lazily and downloads BOM-prefixed with a stamped filename', () => {
    const buildCsv = vi.fn(() => 'a,b\r\n1,2');
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:fake');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(
      <ExportCsvButton
        buildCsv={buildCsv}
        filenamePrefix="acme-export"
        testId="acme-export-button"
        title="t"
      />,
    );
    // Lazy: nothing serialized on render.
    expect(buildCsv).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('acme-export-button'));
    expect(buildCsv).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake');

    const blob = createObjectURL.mock.calls[0]?.[0];
    expect(blob?.type).toContain('text/csv');
  });

  test('disabled blocks the download', () => {
    const buildCsv = vi.fn(() => 'a');
    render(
      <ExportCsvButton
        buildCsv={buildCsv}
        filenamePrefix="x"
        disabled
        testId="x-export-button"
        title="t"
      />,
    );
    const btn = screen.getByTestId('x-export-button');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(buildCsv).not.toHaveBeenCalled();
  });

  test('renders the shared "Export current view" label', () => {
    render(<ExportCsvButton buildCsv={() => ''} filenamePrefix="x" testId="x" title="t" />);
    expect(screen.getByText('Export current view')).toBeInTheDocument();
  });
});
