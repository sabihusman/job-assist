'use client';

import { useEffect, useState } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { ApiKeysSection } from '@/components/settings/ApiKeysSection';
import { AppearanceSection } from '@/components/settings/AppearanceSection';
import { HardRulesSection } from '@/components/settings/HardRulesSection';
import { ManualJobsSection } from '@/components/settings/ManualJobsSection';
import { ProfileSection } from '@/components/settings/ProfileSection';
import { useOperatorProfile } from '@/lib/api/settings';

/**
 * Settings page (PR #32d).
 *
 * Five sections stacked vertically. Each section is independent —
 * Appearance and API keys are pure-frontend; Profile and Hard Rules
 * round-trip the operator_profile endpoint; Manual Jobs POSTs to
 * admin endpoints.
 *
 * Page-only footer renders below the last section (UI_SPEC.md notes
 * Settings is the only page with a contentinfo footer).
 */
export default function SettingsPage() {
  const { data: profile, isLoading, isError, error, refetch } = useOperatorProfile();

  return (
    <AppShell title="Settings" subtitle="Operator tuning interface · single-user">
      <div className="mx-auto flex max-w-3xl flex-col px-6 py-4">
        <AppearanceSection />
        {isError ? (
          <ProfileLoadError
            message={(error as Error)?.message ?? 'Unknown error'}
            onRetry={() => refetch()}
          />
        ) : isLoading || !profile ? (
          <ProfileLoadingSkeleton />
        ) : (
          <>
            <ProfileSection profile={profile} />
            <HardRulesSection profile={profile} />
          </>
        )}
        <ApiKeysSection />
        <ManualJobsSection />
        <SettingsFooter />
      </div>
    </AppShell>
  );
}

function ProfileLoadingSkeleton() {
  return (
    <section className="border-b border-border py-8">
      <div className="h-5 w-32 animate-pulse rounded bg-surface-2" />
      <div className="mt-6 flex flex-col gap-4">
        <div className="h-10 animate-pulse rounded-md border border-border bg-surface-2" />
        <div className="h-32 animate-pulse rounded-md border border-border bg-surface-2" />
      </div>
    </section>
  );
}

function ProfileLoadError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <section className="border-b border-border py-8">
      <h2 className="text-[15px] font-semibold text-negative">
        Couldn&apos;t load operator profile.
      </h2>
      <p className="mt-1 text-[13px] text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex h-8 items-center rounded-md border border-border bg-surface px-3 text-sm hover:bg-accent"
      >
        Retry
      </button>
    </section>
  );
}

/**
 * Settings-only `contentinfo` footer.
 *
 *   job-assist · build {version} · api {label} · last sync {n}s ago
 *
 * - version: read from NEXT_PUBLIC_APP_VERSION if set, else "0.4.0".
 * - api label: derived from NEXT_PUBLIC_API_BASE_URL — "railway-prod"
 *   if the URL contains "railway", else "local".
 * - last sync: ticker from page-mount time. A real liveness ping
 *   could replace this when the API gains a /healthz; for now this
 *   matches the chrome's sync-status pattern.
 */
function SettingsFooter() {
  const version = process.env.NEXT_PUBLIC_APP_VERSION ?? '0.4.0';
  const apiUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
  const apiLabel = apiUrl.includes('railway') ? 'railway-prod' : 'local';
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const interval = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    // The footer lives inside AppShell's <main>, so `<footer>`'s
    // implicit role becomes `generic` rather than `contentinfo`. The
    // E2E spec asserts contentinfo to verify the page-only-footer
    // rule, so we need the explicit role here.
    // biome-ignore lint/a11y/useSemanticElements: see comment above
    <footer
      role="contentinfo"
      className="mt-12 border-t border-border py-6 text-center font-mono text-[11px] text-muted-foreground"
    >
      job-assist · build {version} · api {apiLabel} · last sync {seconds}s ago
    </footer>
  );
}
