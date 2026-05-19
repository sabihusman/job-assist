'use client';

import type { LucideIcon } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * One row in the primary nav. `collapsed=true` hides the label and
 * badge, leaving just the icon (rail mode per UI_SPEC.md).
 *
 * Active matching is exact on `/` (so `/applied` doesn't also light
 * up Triage), prefix match elsewhere. Once #32b adds nested triage
 * routes like `/postings/:id`, the prefix match keeps Triage lit.
 */
export function SidebarItem({
  href,
  label,
  icon: Icon,
  badge,
  collapsed,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  badge?: number;
  collapsed: boolean;
}) {
  const pathname = usePathname();
  const active = href === '/' ? pathname === '/' : pathname.startsWith(href);

  return (
    <Link
      href={href}
      // Pin the accessible name to just the label — otherwise the
      // badge count gets concatenated ("Triage 24") and a11y queries
      // for "Triage" need a regex match. The visible text is unchanged.
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      data-active={active}
      className={cn(
        'group flex h-9 items-center rounded-md px-2 text-sm transition-colors',
        'hover:bg-accent hover:text-accent-foreground',
        active ? 'bg-accent text-accent-foreground font-medium' : 'text-foreground/80',
      )}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      {!collapsed && (
        <>
          <span className="ml-3 flex-1 truncate">{label}</span>
          {badge !== undefined && (
            <span
              aria-hidden="true"
              className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
            >
              {badge}
            </span>
          )}
        </>
      )}
    </Link>
  );
}
