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
 * for v1 per the spec. The Triage badge count is hardcoded to 24 in
 * PR #32a and will read from `GET /postings?state=triage` in PR #32b.
 */
export type NavItem = {
  href: '/' | '/applied' | '/pipeline' | '/companies' | '/stats' | '/settings';
  label: string;
  icon: LucideIcon;
  badge?: number;
};

export const NAV_ITEMS: readonly NavItem[] = [
  { href: '/', label: 'Triage', icon: Inbox, badge: 24 },
  { href: '/applied', label: 'Applied', icon: Activity },
  { href: '/pipeline', label: 'Pipeline', icon: KanbanSquare },
  { href: '/companies', label: 'Companies', icon: Building2 },
  { href: '/stats', label: 'Stats', icon: BarChart3 },
  { href: '/settings', label: 'Settings', icon: SettingsIcon },
] as const;

/**
 * Hardcoded SAVED FILTERS rows for #32a. Each row's `href` is a
 * deep-link onto Triage with a filter slug; #32b will translate the
 * slug into actual `?state=...&tier=...` etc. query params and
 * highlight the active row.
 */
export type SavedFilter = {
  slug: string;
  label: string;
  count: number;
};

export const SAVED_FILTERS: readonly SavedFilter[] = [
  { slug: 't1-remote-not-reviewed', label: 'T1 · Remote · Not reviewed', count: 8 },
  { slug: 'staff-pm-200k', label: 'Staff PM · $200k+', count: 12 },
  { slug: 'snoozed-7d', label: 'Snoozed > 7d', count: 4 },
] as const;
