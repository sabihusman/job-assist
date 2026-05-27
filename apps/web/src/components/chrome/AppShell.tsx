'use client';

import type { ReactNode } from 'react';

import { Banner } from '@/components/chrome/Banner';
import { CommandPalette } from '@/components/chrome/CommandPalette';
import { Sidebar } from '@/components/chrome/Sidebar';

/**
 * Three-region application chrome.
 *
 * Desktop (≥ md):
 *
 *   [sidebar in-place] | [banner sticky 48px]
 *                      | [main scroll region — children]
 *
 * Mobile (< md):
 *
 *   [banner sticky 48px with hamburger]
 *   [main scroll region — children]
 *   [sidebar as off-canvas drawer — opens via hamburger] (UX overhaul PR 1)
 *
 * The flex direction stays ``flex`` (row) at both breakpoints —
 * Sidebar's own ``hidden md:flex`` keeps the in-place rail invisible
 * on mobile, and the mobile drawer is a Sheet rendered to a portal
 * so it doesn't participate in the flex layout. ``min-w-0`` on the
 * inner column prevents long titles / wide tables from forcing
 * horizontal scroll.
 *
 * Composed once per page rather than at the root layout — each page
 * knows its own title/subtitle/adornments without threading them
 * through a Context.
 */
export function AppShell({
  title,
  subtitle,
  adornments,
  children,
}: {
  title: string;
  subtitle?: string;
  adornments?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Banner title={title} subtitle={subtitle} adornments={adornments} />
        <main aria-label="Page content" className="flex-1">
          {children}
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}
