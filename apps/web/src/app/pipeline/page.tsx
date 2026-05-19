import { AppShell } from '@/components/chrome/AppShell';
import { PlaceholderPage } from '@/components/chrome/PlaceholderPage';

export default function PipelinePage() {
  return (
    <AppShell title="Pipeline" subtitle="Open conversations">
      <PlaceholderPage
        heading="Pipeline page — coming in #32c"
        body="Kanban board across interview stages · drag-to-advance · per-column count pills."
      />
    </AppShell>
  );
}
