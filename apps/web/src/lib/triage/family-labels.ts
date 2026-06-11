import type { RoleFamilyWire } from '@/lib/triage/types';

/**
 * Display labels for role_family values.
 *
 * The API returns the snake_case wire form (`product_management`); the
 * UI displays the abbreviated friendly form (`Product Mgmt`). Both
 * directions of the map live here so chip rendering and chip-click
 * handlers share the same dictionary.
 */
export const FAMILY_LABELS: Record<RoleFamilyWire, string> = {
  product_management: 'Product Mgmt',
  product_owner: 'Product Owner',
  product_marketing: 'Product Marketing',
  program_management: 'Program Mgmt',
  // feat/strategy-spine: the warm-path strategy lane. Clicking this chip IS
  // the "strategy view" — an explicit family selection overrides the pm_only
  // gate, so the queue shows strategy_ops roles only (date-sorted by default).
  strategy_ops: 'Strategy/Ops',
  other: 'Other',
};

/** Ordered list for chip rendering — matches the row order in UI_SPEC.md.
 *
 * PR #43 added ``other`` so the operator can filter on the bucket the
 * classifier produces for uncategorised roles (which is most postings
 * right now — classifier improvements are PR #44). Without this chip
 * the bulk of the queue was invisible to chip-based narrowing.
 */
export const FAMILY_CHIPS: readonly RoleFamilyWire[] = [
  'product_management',
  'product_owner',
  'product_marketing',
  'program_management',
  'strategy_ops',
  'other',
] as const;

/** Look up a friendly label for a wire value; falls back to the wire form. */
export function familyLabel(wire: string | null | undefined): string {
  if (!wire) return '—';
  return FAMILY_LABELS[wire as RoleFamilyWire] ?? wire;
}
