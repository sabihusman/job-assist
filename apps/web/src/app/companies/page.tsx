import { AppShell } from '@/components/chrome/AppShell';
import { PlaceholderPage } from '@/components/chrome/PlaceholderPage';

export default function CompaniesPage() {
  return (
    <AppShell title="Companies" subtitle="Target list">
      <PlaceholderPage
        heading="Companies page — coming in #32c"
        body="Tier · ATS coverage · active vs total postings · outreach history. The '+ Add company' button observed in the Lovable build is stripped from v1."
      />
    </AppShell>
  );
}
