/**
 * Shared CSV building + download helpers (feat/view-exports).
 *
 * One RFC-4180 serializer for every tab's "Export current view" so the cell
 * escaping, line endings, and Excel-friendly BOM handling can't drift between
 * tabs. Extracted from the Pipeline export (feat/pipeline-export), which now
 * builds on these primitives — its output is byte-identical to before.
 */

/** RFC-4180 escape: quote a cell iff it contains a comma, quote, CR or LF. */
export function csvCell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/**
 * Serialize a header row + data rows to an RFC-4180 CSV string (CRLF line
 * endings, BOM-free — the download path prepends the BOM so pure-function
 * tests stay byte-exact). ``null``/``undefined`` cells render empty.
 */
export function buildCsv(
  headers: readonly string[],
  rows: ReadonlyArray<ReadonlyArray<string | number | null | undefined>>,
): string {
  const lines: string[] = [headers.map((h) => csvCell(h)).join(',')];
  for (const row of rows) {
    lines.push(row.map((cell) => csvCell(String(cell ?? ''))).join(','));
  }
  return lines.join('\r\n');
}

/** `YYYYMMDD-HHMMSS` local-time stamp for export filenames. */
export function exportTimestamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/**
 * Trigger a browser download of *csv* as *filename*. BOM-prefixed so Excel
 * reads UTF-8 cleanly; the Blob keeps the pure builder BOM-free.
 */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
