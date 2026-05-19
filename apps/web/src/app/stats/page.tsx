import { AppShell } from '@/components/chrome/AppShell';
import { PlaceholderPage } from '@/components/chrome/PlaceholderPage';

export default function StatsPage() {
  return (
    <AppShell title="Stats" subtitle="Operator funnel">
      <PlaceholderPage
        heading="Stats page — coming in #32c"
        body="Funnel viz from /stats/funnel · KPIs from /stats/calibration · window picker. SOURCE EFFECTIVENESS panel from the source build is stripped."
      />
    </AppShell>
  );
}
