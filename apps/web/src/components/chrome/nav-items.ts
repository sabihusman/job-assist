import {
  Activity,
  BarChart3,
  Building2,
  Inbox,
  KanbanSquare,
  type LucideIcon,
  Settings as SettingsIcon,
} from 'lucide-react';

/**
 * Primary nav inventory. Outreach is intentionally absent — stripped
 * for v1 per the spec. The Triage badge count was a placeholder in
 * PR #32a; #32b removes it and the Triage page renders its own count
 * via the filter rail.
 */
export type NavItem = {
  href: '/' | '/applied' | '/pipeline' | '/companies' | '/stats' | '/settings';
  label: string;
  icon: LucideIcon;
  badge?: number;
};

export const NAV_ITEMS: readonly NavItem[] = [
  { href: '/', label: 'Triage', icon: Inbox },
  { href: '/applied', label: 'Applied', icon: Activity },
  { href: '/pipeline', label: 'Pipeline', icon: KanbanSquare },
  { href: '/companies', label: 'Companies', icon: Building2 },
  { href: '/stats', label: 'Stats', icon: BarChart3 },
  { href: '/settings', label: 'Settings', icon: SettingsIcon },
] as const;

/**
 * Hardcoded SAVED FILTERS rows. Each row's `href` is the resolved
 * Triage URL with `?state=...&tier=...` etc. `filterParams` is the
 * same param set as a plain object so the count badge query (in
 * SavedFilters.tsx) can hit `/postings?…&limit=1` and read `.total`.
 *
 * Row #2 was "Staff PM · $200k+" in #32a — but `GET /postings` has no
 * seniority or salary_min filter. Substituted for "T1+T2 · PM" which
 * we can actually express today; revisit when the API gains those
 * filter capabilities.
 */
export type SavedFilter = {
  slug: string;
  label: string;
  /** Resolved query string for the Triage URL. */
  href: `/?${string}`;
  /** Params object suitable for `useSavedFilterCount`. */
  filterParams: Record<string, unknown>;
};

export const SAVED_FILTERS: readonly SavedFilter[] = [
  {
    slug: 't1-remote-not-reviewed',
    label: 'T1 · Remote · Not reviewed',
    href: '/?tier=1&remote_type=remote&state=triage',
    filterParams: { tier: [1], remote_type: ['remote'], state: ['triage'] },
  },
  {
    slug: 't1-t2-pm',
    label: 'T1+T2 · PM',
    href: '/?tier=1&tier=2&role_family=product_management&state=triage',
    filterParams: {
      tier: [1, 2],
      role_family: ['product_management'],
      state: ['triage'],
    },
  },
  {
    slug: 'snoozed-7d',
    label: 'Snoozed > 7d',
    href: '/?state=snoozed&include_snoozed_past_only=true',
    filterParams: {
      state: ['snoozed'],
      include_snoozed_past_only: true,
    },
  },
] as const;
