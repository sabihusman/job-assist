'use client';

import { useCallback } from 'react';

import { downloadCsv, exportTimestamp } from '@/lib/csv';

/**
 * Shared "Export current view" button for client-composed views
 * (feat/view-exports). Mirrors the Triage export's intent — download exactly
 * what's on screen, every row, no cap — but client-side: these views are
 * already derived in the browser, so we serialize the in-memory rows rather
 * than round-tripping to a backend endpoint that would have to re-implement
 * (and could drift from) the view's composition logic.
 *
 * ``buildCsv`` is called lazily on click so the button never serializes on
 * render; ``filenamePrefix`` becomes ``<prefix>-<YYYYMMDD-HHMMSS>.csv``.
 * Server-filtered lists (Triage, Passed, Contacts) use their endpoint-backed
 * export links instead — same visual treatment, full unpaginated set.
 */
export function ExportCsvButton({
  buildCsv,
  filenamePrefix,
  disabled = false,
  testId,
  title,
}: {
  buildCsv: () => string;
  filenamePrefix: string;
  disabled?: boolean;
  testId: string;
  title: string;
}) {
  const onClick = useCallback(() => {
    downloadCsv(`${filenamePrefix}-${exportTimestamp()}.csv`, buildCsv());
  }, [buildCsv, filenamePrefix]);

  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="inline-flex shrink-0 items-center gap-1.5 rounded border border-border bg-surface px-2.5 py-1 text-sm text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      Export current view
    </button>
  );
}
