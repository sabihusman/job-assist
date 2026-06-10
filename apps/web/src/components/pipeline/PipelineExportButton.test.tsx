import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { PipelineExportButton } from '@/components/pipeline/PipelineExportButton';
import { PIPELINE_BOARD_STAGES } from '@/lib/applied/stages';
import { emptyBuckets } from '@/lib/pipeline/bucket';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('PipelineExportButton', () => {
  test('disabled when the board is empty', () => {
    render(<PipelineExportButton buckets={emptyBuckets()} order={PIPELINE_BOARD_STAGES} />);
    expect(screen.getByTestId('pipeline-export-button')).toBeDisabled();
  });

  test('clicking downloads a UTF-8 CSV named pipeline-export-*.csv', () => {
    const createObjectURL = vi.fn((_blob: Blob): string => 'blob:mock');
    const revokeObjectURL = vi.fn();
    // jsdom doesn't implement these — provide them.
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeObjectURL;

    let downloadName = '';
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloadName = this.download;
    });

    const b = emptyBuckets();
    b.applied.push({
      id: '1',
      companyName: 'Acme',
      roleTitle: 'PM',
      roleFamily: null,
      appliedAt: '2026-01-01T00:00:00Z',
    });

    render(<PipelineExportButton buckets={b} order={PIPELINE_BOARD_STAGES} />);
    const btn = screen.getByTestId('pipeline-export-button');
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(downloadName).toMatch(/^pipeline-export-\d{8}-\d{6}\.csv$/);
  });
});
