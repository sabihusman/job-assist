/**
 * Deterministic name → avatar background.
 *
 * UI_SPEC.md catalogues the Lovable build's avatar pattern (warm /
 * orange-red for Linear/Stripe/Notion/PostHog, green for Vercel). The
 * exact palette is incidental; what matters is determinism — the same
 * company name always maps to the same hue across renders, sessions,
 * and clients.
 *
 * Strategy: FNV-1a over the company name → hue in [0, 360). Pinned
 * lightness/chroma keeps every avatar legible against white text.
 */

function fnv1a(input: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    // 32-bit FNV prime, kept inside the safe-integer range.
    hash = (hash * 0x01000193) >>> 0;
  }
  return hash;
}

/** Hue in [0, 360) for the given (case-insensitive, trimmed) name. */
export function hueFor(name: string): number {
  const seed = name.trim().toLowerCase();
  if (!seed) return 0;
  return fnv1a(seed) % 360;
}

/**
 * Full oklch CSS string for an avatar background. Lightness + chroma are
 * fixed to UI_SPEC.md's reference values (0.62 / 0.13) so colors stay
 * inside the spec's warm-mid range.
 */
export function avatarBg(name: string): string {
  const hue = hueFor(name);
  return `oklch(0.62 0.13 ${hue})`;
}

/** Single uppercase initial — empty string returns '?'. */
export function avatarInitial(name: string): string {
  const trimmed = name.trim();
  return trimmed.length === 0 ? '?' : trimmed.charAt(0).toUpperCase();
}
