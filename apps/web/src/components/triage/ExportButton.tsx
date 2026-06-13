'use client';

import { useSearchParams } from 'next/navigation';

import { API_BASE_URL } from '@/lib/api/client';
import { encodeFilters, parseFilters, resolveRoleFamilies } from '@/lib/triage/filters';

/**
 * "Export current view" button on the Triage page (feat/triage-export-xlsx).
 *
 * Renders as a plain `<a href>` so the browser's native download handles
 * the Content-Disposition response — no fetch + blob + URL.createObjectURL
 * dance, no JS error path to worry about, works exactly the way Save-As
 * already works for every other download in the browser.
 *
 * The export URL is built through the SAME parse→encode round-trip the list
 * view uses (lib/triage/filters), NOT a verbatim copy of the raw URL params.
 * That matters because the Triage view's filters carry RESOLVED DEFAULTS that
 * never appear in the URL — chiefly ``state=['triage']`` (parseFilters) and the
 * ``pm_only`` → PM/PO ``role_family`` resolution. Forwarding the raw params
 * dropped the implicit ``state=triage`` default, so a bare triage URL exported
 * EVERY state (applied/rejected/snoozed too) instead of only the pending rows
 * the operator sees. Routing through parse→encode reproduces every default in
 * one place, so the export can't silently diverge from the list again.
 */
export function ExportButton() {
  const searchParams = useSearchParams();
  const filters = parseFilters(searchParams);
  const params = encodeFilters(filters);
  // pm_only is a frontend-only concept the API doesn't understand; encodeFilters
  // may emit it (pm_only=false), so strip it and re-append the RESOLVED family
  // set the list query actually sends — explicit chips, else the PM/PO default,
  // else nothing (gate off → all families).
  params.delete('pm_only');
  params.delete('role_family');
  for (const fam of resolveRoleFamilies(filters)) params.append('role_family', fam);
  const query = params.toString();
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
      title="Download an .xlsx of every row currently visible — same filters, same sort, no row cap."
    >
      Export current view
    </a>
  );
}
