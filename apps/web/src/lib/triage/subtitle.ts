/**
 * Banner subtitle helper for the Triage page (PR #43).
 *
 * The old subtitle was the hardcoded string ``"Pending review"``. It
 * now reflects the live pending + applied counts so the operator can
 * see at a glance how big each bucket is.
 *
 *   - both loading                          → "loading…"
 *   - pending loaded, applied unknown       → "{N} pending"
 *   - both loaded                           → "{N} pending · {M} applied"
 *   - either errored                        → "Pending review" (legacy fallback)
 *
 * Lives outside ``app/page.tsx`` because Next.js disallows arbitrary
 * named exports from page files.
 */
export function computeSubtitle({
  pendingTotal,
  appliedTotal,
  isPendingLoading,
  isError,
}: {
  pendingTotal: number | null;
  appliedTotal: number | null;
  isPendingLoading: boolean;
  isError: boolean;
}): string {
  if (isError) return 'Pending review';
  if (isPendingLoading || pendingTotal === null) return 'loading…';
  if (appliedTotal === null) return `${pendingTotal} pending`;
  return `${pendingTotal} pending · ${appliedTotal} applied`;
}
