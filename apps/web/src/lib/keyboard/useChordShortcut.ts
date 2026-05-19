'use client';

/**
 * Two-key chord shortcut hook — stubbed for PR #32a.
 *
 * The command palette displays chord hints like `G T`, `G A`, `G P` for
 * navigation, but the Lovable source build doesn't wire them globally.
 * UI_SPEC.md flags this as a port-time decision; we ship the hints in
 * v1 and leave activation to a follow-up PR (#32b or later).
 *
 * Exported here so call sites that try to import it don't break, and
 * future PRs can fill in the body without changing the signature.
 */
type ChordHandler = (e: KeyboardEvent) => void;

export function useChordShortcut(
  // First key in the chord (e.g. "g")
  _leader: string,
  // Map of second key → handler (e.g. { t: goTo("/"), a: goTo("/applied") })
  _bindings: Record<string, ChordHandler>,
  // Window after leader during which the second key must arrive
  _timeoutMs = 800,
): void {
  // Intentionally a no-op for #32a. Activate in a future PR.
}
