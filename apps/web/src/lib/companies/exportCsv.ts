import type { CompanyListItem } from '@/lib/companies/types';
import { buildCsv } from '@/lib/csv';

/**
 * CSV of the CURRENT Companies view (feat/view-exports). The table renders
 * the full company list from one fetch (no pagination), so this is a pure
 * serialization of the rows on screen, in their on-screen order.
 */

const HEADERS = [
  'name',
  'tier',
  'ats',
  'source',
  'active_postings',
  'total_postings',
  'applications',
  'last_applied',
  'notes',
] as const;

export function buildCompaniesCsv(items: readonly CompanyListItem[]): string {
  const rows = items.map((c) => [
    c.name,
    c.tier ?? '',
    c.ats_set.length > 0 ? c.ats_set.join('; ') : (c.ats ?? ''),
    c.source ?? '',
    c.active_postings,
    c.total_postings,
    c.application_count ?? 0,
    c.last_applied_at ? c.last_applied_at.slice(0, 10) : '',
    c.notes ?? '',
  ]);
  return buildCsv(HEADERS, rows);
}
