'use client';

import type { ReactNode } from 'react';

import { Banner } from '@/components/chrome/Banner';
import { CommandPalette } from '@/components/chrome/CommandPalette';
import { Sidebar } from '@/components/chrome/Sidebar';

/**
 * Three-region application chrome:
 *
 *   [sidebar] | [banner sticky 48px]
 *             | [main scroll region — children]
 *
 * Composed once per page rather than at the root layout. Reasoning:
 * each page knows its own title/subtitle/adornments, and threading
 * those through a layout would force a Context dance. Pages just
 * render `<AppShell title="…">{content}</AppShell>` and Next.js's
 * layout still handles fonts / providers above this.
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
