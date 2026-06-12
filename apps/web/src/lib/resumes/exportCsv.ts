import type { ResumeAnalytics, ResumeVersion } from '@/lib/api/resume';
import { buildCsv } from '@/lib/csv';

/**
 * CSV of the CURRENT Resumes view (feat/view-exports): one row per resume
 * version, joining the version registry (label/angle/notes/created) with the
 * outcome analytics table the page renders (apps, companies,
 * rejected/confirmed). Versions the analytics hasn't attributed yet export
 * with zero counts — same as the on-screen table's empty cells.
 */

const HEADERS = [
  'label',
  'angle',
  'applications',
  'companies',
  'companies_rejected',
  'companies_confirmed',
  'notes',
  'created',
] as const;

export function buildResumesCsv(
  versions: readonly ResumeVersion[],
  analytics: ResumeAnalytics | undefined,
): string {
  const byId = new Map(analytics?.by_version.map((v) => [v.resume_version_id, v]) ?? []);
  const rows = versions.map((v) => {
    const a = byId.get(v.id);
    return [
      v.label,
      v.angle ?? '',
      a?.applications ?? 0,
      a?.companies ?? 0,
      a?.companies_rejected ?? 0,
      a?.companies_confirmed ?? 0,
      v.notes ?? '',
      v.created_at.slice(0, 10),
    ];
  });
  return buildCsv(HEADERS, rows);
}
