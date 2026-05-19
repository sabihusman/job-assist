import { AppShell } from '@/components/chrome/AppShell';
import { PlaceholderPage } from '@/components/chrome/PlaceholderPage';

export default function AppliedPage() {
  return (
    <AppShell title="Applied" subtitle="Sent and waiting">
      <PlaceholderPage
        heading="Applied page — coming in #32c"
        body="Chronological list of applications · linked outcomes from Gmail polling · per-company grouping."
      />
    </AppShell>
  );
}
