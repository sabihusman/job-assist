import { STAGE_LABELS } from '@/lib/applied/stages';
import { type UnifiedAppliedEntry, entryStage, entryStatusLabel } from '@/lib/applied/unify';
import { buildCsv } from '@/lib/csv';

/**
 * CSV of the CURRENT unified Applied/Rejected view (feat/view-exports).
 *
 * Both pages compose their list client-side (`unifyApplied` merges manual
 * posting-state rows with the full Gmail outcome set, then sorts/filters), so
 * "export what you see" is a pure serialization of the already-unified
 * entries — same data, same dedupe, same on-screen order. The status column
 * resolves exactly like the on-screen pill: the manual status is
 * authoritative; otherwise the Gmail stage label.
 */

const HEADERS = ['source', 'company', 'role', 'status', 'last_activity', 'emails'] as const;

export function buildUnifiedCsv(entries: readonly UnifiedAppliedEntry[]): string {
  const rows = entries.map((entry) => [
    entry.source,
    entry.company,
    entry.role ?? '',
    entryStatusLabel(entry) ?? STAGE_LABELS[entryStage(entry)],
    new Date(entry.at).toISOString().slice(0, 10),
    entry.events.length,
  ]);
  return buildCsv(HEADERS, rows);
}
