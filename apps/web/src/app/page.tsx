'use client';

import { useEffect } from 'react';

import { AppShell } from '@/components/chrome/AppShell';
import { PlaceholderPage } from '@/components/chrome/PlaceholderPage';
import { usePostings } from '@/lib/api/hooks';

/**
 * Triage page placeholder (PR #32a).
 *
 * Wires the API smoke test from the spec — fires `GET /postings`,
 * discards the result. Used to verify the openapi-fetch + react-query
 * wiring round-trips end-to-end against the live backend. PR #32b
 * replaces this with the real card list + detail panel.
 */
export default function TriagePage() {
  const { isError, error } = usePostings();

  useEffect(() => {
    if (isError) {
      console.warn('[smoke] GET /postings failed — is NEXT_PUBLIC_API_BASE_URL set?', error);
    }
  }, [isError, error]);

  return (
    <AppShell title="Triage" subtitle="Pending review · 24">
      <PlaceholderPage
        heading="Triage page — coming in #32b"
        body="Filter rail · card list · keyboard-driven detail panel · J/K nav · reason picker. The chrome (sidebar, banner, ⌘K palette, theme) is wired here in #32a; the page body lands in #32b."
      />
    </AppShell>
  );
}
