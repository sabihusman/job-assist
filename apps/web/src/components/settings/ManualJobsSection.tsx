import { ManualJobRow } from '@/components/settings/ManualJobRow';
import { SettingsSection } from '@/components/settings/layout';

/**
 * Manual job triggers. The three admin endpoints were all confirmed
 * to exist during the PR #32d read-first audit:
 *
 *   POST /admin/discover-ats/run?commit=false
 *   POST /admin/gmail/backfill?days=60
 *   POST /admin/ingest/greenhouse/{handle}
 *
 * If any one of these is removed from the backend in a future PR,
 * delete its row here.
 */
export function ManualJobsSection() {
  return (
    <SettingsSection
      heading="Manual job triggers"
      description="POSTs to backend admin endpoints. Output stays in place."
    >
      <div className="flex flex-col gap-3">
        <ManualJobRow
          title="Run discover-ats"
          endpoint="/admin/discover-ats/run?commit=false"
          job="discover-ats"
        />
        <ManualJobRow
          title="Run Gmail backfill (60 days)"
          endpoint="/admin/gmail/backfill?days=60"
          job="gmail-backfill"
        />
        <ManualJobRow
          title="Run Greenhouse ingestion"
          endpoint="/admin/ingest/greenhouse/{handle}"
          job="greenhouse-ingest"
          inputPlaceholder="handle"
        />
      </div>
    </SettingsSection>
  );
}
