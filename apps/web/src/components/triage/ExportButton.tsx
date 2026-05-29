'use client';

import { useSearchParams } from 'next/navigation';

import { API_BASE_URL } from '@/lib/api/client';

/**
 * "Export view (top 40)" button on the Triage page (feat/triage-export-xlsx).
 *
 * Renders as a plain `<a href>` so the browser's native download handles
 * the Content-Disposition response — no fetch + blob + URL.createObjectURL
 * dance, no JS error path to worry about, works exactly the way Save-As
 * already works for every other download in the browser. Same searchParams
 * the user's currently viewing are appended verbatim so the export ==
 * what they see (per backend's shared query helper).
 */
export function ExportButton() {
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const href = `${API_BASE_URL}/postings/export.xlsx${query ? `?${query}` : ''}`;
  return (
    <a
      href={href}
      // Defensive: some browsers honor download on cross-origin only when
      // the server sets Content-Disposition (which we do); the attribute
      // is a hint, not a requirement here.
      download
      data-testid="triage-export-button"
      className="inline-flex shrink-0 items-center gap-1.5 rounded border border-border bg-surface px-2.5 py-1 text-sm text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground"
      title="Download an .xlsx of the top 40 rows currently visible — same filters, same sort."
    >
      Export view (top 40)
    </a>
  );
}
